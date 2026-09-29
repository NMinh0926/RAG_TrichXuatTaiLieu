"""Ingest worker: `python -m app.worker`.

Loads the embedding model and OCR engine once, re-queues jobs interrupted by a previous crash, then
processes the Redis 'ingest' queue. RQ's SimpleWorker runs jobs in this process (no fork per job), so
the loaded models are reused across jobs.
"""
import logging
import sys
import uuid
from collections.abc import Callable
from functools import lru_cache

from app.core.config import get_settings
from app.core.logging import configure_logging

logger = logging.getLogger('worker')


@lru_cache
def get_runner():
    from app.core.dependencies import get_services, get_storage
    from app.db.session import get_sessionmaker
    from app.services.jobs import IngestRunner
    return IngestRunner(get_sessionmaker(), get_storage(), get_services()[0])


def run_ingest_job(job_id: str) -> None:
    """RQ entry point (enqueued by the API as 'app.worker.run_ingest_job')."""
    get_runner().run(uuid.UUID(job_id))


def recover(session_factory, queue, waiting: Callable[[str], bool]) -> int:
    """Re-enqueue jobs left queued/running in PostgreSQL that Redis no longer holds as waiting.

    Called once at worker start, before any job runs, so a 'running' row is from a crashed worker.
    """
    from app.db.models import IngestJob, JobStatus
    recovered = 0
    with session_factory() as session:
        for job in session.query(IngestJob).filter(IngestJob.status.in_([JobStatus.queued, JobStatus.running])):
            if job.status == JobStatus.queued and waiting(str(job.id)):
                continue
            job.status, job.step, job.progress, job.started_at = JobStatus.queued, 'queued', 0, None
            session.commit()
            queue.enqueue(job.id)
            recovered += 1
    return recovered


def waiting_in_redis(connection) -> Callable[[str], bool]:
    from rq.exceptions import NoSuchJobError
    from rq.job import Job

    def waiting(job_id: str) -> bool:
        try:
            return Job.fetch(job_id, connection=connection).get_status() == 'queued'
        except NoSuchJobError:
            return False
    return waiting


def main() -> int:
    configure_logging()
    from rq import SimpleWorker
    from app.core.dependencies import get_job_queue, get_services
    from app.db.session import get_sessionmaker
    settings = get_settings()
    ingestion, _ = get_services()
    ingestion.embedding.model  # load once, before the first job
    logger.info('worker ready embedding=%s ocr_lang=%s', settings.embedding_model, settings.ocr_lang)
    job_queue = get_job_queue()
    connection = job_queue.queue.connection
    logger.info('recovered jobs=%d', recover(get_sessionmaker(), job_queue, waiting_in_redis(connection)))
    SimpleWorker([job_queue.queue], connection=connection).work(with_scheduler=False)
    return 0


if __name__ == '__main__':
    sys.exit(main())
