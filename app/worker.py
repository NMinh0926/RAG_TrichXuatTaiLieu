"""Ingest worker: `python -m app.worker`.

Loads the embedding model and OCR engine once, then processes the Redis 'ingest' queue. RQ's SimpleWorker runs
jobs in this process (no fork per job), so the loaded models are reused across jobs. At start and every
RECOVERY_INTERVAL_SECONDS between jobs, the worker re-enqueues jobs that were lost from Redis or abandoned by a
crashed worker; this is safe with several workers (`--scale worker=N`).
"""
import logging
import sys
import uuid
from collections.abc import Callable
from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.models import IngestJob, JobStatus
from app.services.jobs import JobQueue, RQJobQueue, locked_documents, mark_failed

logger = logging.getLogger('worker')
RECOVERY_INTERVAL_SECONDS = 60
# An idle worker blocks on Redis for worker_ttl - 15 s between maintenance checks; this keeps recovery close to
# its interval even when no job arrives.
WORKER_TTL_SECONDS = 90
# A job whose worker died this many times while running it (memory exhaustion, native crash on a malformed
# file) is failed instead of retried, so one file cannot crash-loop the worker.
MAX_WORKER_CRASHES = 2
RESTART_WARNING = {'page': None, 'step': 'worker', 'error': 'worker_restarted'}
CRASHED_MESSAGE = ('Tiến trình xử lý bị dừng đột ngột nhiều lần với tài liệu này '
                   '(file có thể quá lớn hoặc bị hỏng).')


@lru_cache
def get_runner():
    from app.core.dependencies import get_services, get_storage
    from app.db.session import get_sessionmaker
    from app.services.jobs import IngestRunner
    return IngestRunner(get_sessionmaker(), get_storage(), get_services()[0])


def run_ingest_job(job_id: str) -> None:
    """RQ entry point (enqueued by the API as 'app.worker.run_ingest_job')."""
    get_runner().run(uuid.UUID(job_id))


def recover(session_factory: Callable[[], Session], queue: JobQueue, waiting: Callable[[str], bool],
            delete_vectors: Callable[[str], None] | None = None) -> int:
    """Re-enqueue unfinished jobs that no runner is processing and Redis no longer holds as waiting.

    A job whose document lock is held is being processed (by any worker) and is left alone. A 'running' job
    without the lock was abandoned by a dead worker: it is retried, and failed after MAX_WORKER_CRASHES deaths.
    Returns the number of jobs enqueued again.
    """
    with session_factory() as session:
        candidates = session.execute(select(IngestJob.id).where(
            IngestJob.status.in_([JobStatus.queued, JobStatus.running]))).scalars().all()
    recovered = 0
    for job_id in candidates:
        if _recover_job(session_factory, job_id, waiting, delete_vectors):
            queue.enqueue(job_id)
            recovered += 1
    return recovered


def _recover_job(session_factory: Callable[[], Session], job_id: uuid.UUID, waiting: Callable[[str], bool],
                 delete_vectors: Callable[[str], None] | None) -> bool:
    """Decide (and record) what happens to one unfinished job; True if it must be enqueued again."""
    with session_factory() as session:
        # Row lock: concurrent recoveries (several workers) never handle the same job twice.
        job = session.scalar(select(IngestJob).where(IngestJob.id == job_id).with_for_update(skip_locked=True))
        if (job is None or job.status not in (JobStatus.queued, JobStatus.running)
                or locked_documents(session, [job.document_id])):
            return False
        if job.status == JobStatus.queued:
            return not waiting(str(job.id))
        document_id = job.document_id
        crashes = 1 + sum(warning.get('error') == RESTART_WARNING['error'] for warning in job.warnings or [])
        if crashes >= MAX_WORKER_CRASHES:
            mark_failed(job, 'worker_crashed', CRASHED_MESSAGE)
            session.commit()
            logger.error('ingest job failed after worker crashes job_id=%s crashes=%d', job_id, crashes)
        else:
            job.warnings = [*(job.warnings or []), RESTART_WARNING]
            job.status, job.step, job.progress, job.started_at = JobStatus.queued, 'queued', 0, None
            session.commit()
            logger.warning('ingest job abandoned by a dead worker, retrying job_id=%s', job_id)
            return True
    if delete_vectors is not None:
        try:
            delete_vectors(str(document_id))
        except Exception:
            logger.warning('vector cleanup failed job_id=%s', job_id)
    return False


def waiting_in_redis(connection) -> Callable[[str], bool]:
    from rq.exceptions import NoSuchJobError
    from rq.job import Job

    def waiting(job_id: str) -> bool:
        try:
            return Job.fetch(job_id, connection=connection).get_status() == 'queued'
        except NoSuchJobError:
            return False
    return waiting


def build_worker(job_queue: RQJobQueue, recovery: Callable[[], None]):
    """SimpleWorker that also runs `recovery` with RQ's maintenance: at start, then between jobs and while idle."""
    from rq import SimpleWorker

    class IngestWorker(SimpleWorker):
        def run_maintenance_tasks(self) -> None:
            super().run_maintenance_tasks()
            recovery()

    return IngestWorker([job_queue.queue], connection=job_queue.queue.connection,
                        maintenance_interval=RECOVERY_INTERVAL_SECONDS, worker_ttl=WORKER_TTL_SECONDS)


def main() -> int:
    configure_logging()
    from app.core.dependencies import get_job_queue, get_services
    from app.db.session import get_sessionmaker
    settings = get_settings()
    ingestion, _ = get_services()
    ingestion.embedding.model  # load once, before the first job
    logger.info('worker ready embedding=%s ocr_lang=%s', settings.embedding_model, settings.ocr_lang)
    job_queue = get_job_queue()
    waiting = waiting_in_redis(job_queue.queue.connection)

    def recovery() -> None:
        # Never fatal: a database or Redis outage is retried at the next maintenance round.
        try:
            recovered = recover(get_sessionmaker(), job_queue, waiting, ingestion.store.delete_document)
        except Exception:
            logger.exception('job recovery failed')
            return
        if recovered:
            logger.info('recovered jobs=%d', recovered)
    build_worker(job_queue, recovery).work(with_scheduler=False)
    return 0


if __name__ == '__main__':
    sys.exit(main())
