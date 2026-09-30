"""Background ingest: the API enqueues a job id on Redis (RQ); the worker runs IngestRunner.run(job_id).

Progress (percent + current step) and non-fatal per-page warnings are written to `ingest_jobs`,
so any API process can report them (GET /api/jobs/{id}, SSE /api/jobs/{id}/events).

A runner holds a PostgreSQL advisory lock on the document for the whole job. The lock makes a job delivered
twice (several workers, recovery) run once, and tells the API and the worker's recovery which unfinished jobs
are really being processed: PostgreSQL releases it when a crashed worker's connection closes.
"""
import logging
import time
import uuid
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.errors import PipelineError
from app.db.models import Chunk, Document, DocumentStatus, IngestJob, JobStatus
from app.db.repositories import DocumentRepository
from app.services.ingestion import IngestionService
from app.storage.base import Storage

logger = logging.getLogger(__name__)
# Progress bands per step (percent at step start); extraction and embedding advance inside their band.
STEPS = {'queued': 0, 'download': 2, 'extract': 5, 'chunk': 60, 'embed': 62, 'index': 96, 'done': 100}


def document_lock_key(document_id: uuid.UUID) -> int:
    """Advisory-lock key of a document: the first 64 bits of its UUID as a signed bigint."""
    return int.from_bytes(document_id.bytes[:8], 'big', signed=True)


def try_lock_document(session: Session, document_id: uuid.UUID) -> bool:
    """Take the document's processing lock until the session's transaction ends; False if another runner holds it."""
    return bool(session.scalar(text('SELECT pg_try_advisory_xact_lock(:key)'), {'key': document_lock_key(document_id)}))


def locked_documents(session: Session, document_ids: Iterable[uuid.UUID]) -> set[uuid.UUID]:
    """The documents a runner is processing right now. Reads pg_locks only, so it never takes a lock itself."""
    wanted = {document_lock_key(document_id) & 0xFFFF_FFFF_FFFF_FFFF: document_id for document_id in document_ids}
    if not wanted:
        return set()
    # A bigint advisory key is reported as classid (high 32 bits) and objid (low 32 bits) with objsubid 1.
    rows = session.execute(text("SELECT classid::bigint, objid::bigint FROM pg_locks WHERE locktype = 'advisory' "
                                'AND objsubid = 1 AND granted '
                                'AND database = (SELECT oid FROM pg_database WHERE datname = current_database())'))
    return {wanted[key] for high, low in rows if (key := high << 32 | low) in wanted}


def mark_failed(job: IngestJob, code: str, message: str) -> None:
    """Terminal failure of a job and of its document (the caller commits)."""
    job.status, job.error_code, job.error_message = JobStatus.failed, code, message
    job.finished_at = datetime.now(timezone.utc)
    if job.document is not None:
        job.document.status, job.document.error_message = DocumentStatus.failed, message


class JobQueue(Protocol):
    def enqueue(self, job_id: uuid.UUID) -> None: ...


class RQJobQueue:
    def __init__(self, redis_url: str, timeout_seconds: int):
        from redis import Redis
        from rq import Queue
        self.queue = Queue('ingest', connection=Redis.from_url(redis_url))
        self.timeout_seconds = timeout_seconds

    def enqueue(self, job_id: uuid.UUID) -> None:
        try:
            self.queue.enqueue('app.worker.run_ingest_job', str(job_id), job_id=str(job_id),
                               job_timeout=self.timeout_seconds, result_ttl=3600, failure_ttl=86400)
        except Exception as exc:
            raise PipelineError('queue_unavailable', 'Hàng đợi xử lý (Redis) không khả dụng.', 503) from exc

    def ping(self) -> None:
        try:
            self.queue.connection.ping()
        except Exception as exc:
            raise PipelineError('queue_unavailable', 'Hàng đợi xử lý (Redis) không khả dụng.', 503) from exc


class InlineJobQueue:
    """Runs the job immediately in this process: one-off scripts (migration, re-index), tests and
    isolated benchmarks, which must not hand work to the shared worker."""

    def __init__(self, runner: 'IngestRunner'):
        self.runner = runner

    def enqueue(self, job_id: uuid.UUID) -> None:
        self.runner.run(job_id)


class Progress:
    """Writes step/percent to the job row, at most every `interval` seconds unless the step changes."""

    def __init__(self, session_factory: Callable[[], Session], job_id: uuid.UUID, interval: float = 0.5):
        self.session_factory, self.job_id, self.interval = session_factory, job_id, interval
        self.step, self.percent, self.written_at = '', -1, 0.0
        self.warnings: list[dict] = []
        # Seconds spent per step, e.g. {"download": 0.2, "extract": 48.1, ...}.
        self.timings: dict[str, float] = {}
        self.step_started = time.monotonic()

    def finish(self) -> dict[str, float]:
        """Close the current step and return all step durations."""
        if self.step:
            self.timings[self.step] = round(self.timings.get(self.step, 0) + time.monotonic() - self.step_started, 2)
            self.step = ''
        return self.timings

    def __call__(self, step: str, fraction: float = 0.0) -> None:
        ceiling = next((value for name, value in STEPS.items() if value > STEPS[step]), 100)
        percent = min(99, int(STEPS[step] + (ceiling - STEPS[step]) * max(0.0, min(1.0, fraction))))
        now = time.monotonic()
        if step != self.step:
            self.finish()
            self.step_started = now
        elif percent == self.percent or now - self.written_at < self.interval:
            return
        self.step, self.percent, self.written_at = step, percent, now
        with self.session_factory() as session:
            job = session.get(IngestJob, self.job_id)
            if job is not None:
                job.step, job.progress = step, max(job.progress, percent)
                session.commit()

    def warn(self, warning: dict) -> None:
        self.warnings.append(warning)


class IngestRunner:
    def __init__(self, session_factory: Callable[[], Session], storage: Storage, ingestion: IngestionService):
        self.session_factory, self.storage, self.ingestion = session_factory, storage, ingestion

    def run(self, job_id: uuid.UUID) -> None:
        """Process one queued job. Failures mark job and document failed and leave no vectors behind.

        A job that is no longer queued (already run, superseded) is skipped. A job whose document another runner
        holds stays queued, and the worker's recovery enqueues it again once the document is free. A database error
        before the document is locked propagates and leaves the row queued for that recovery too.
        """
        with self.session_factory() as session:
            job = session.get(IngestJob, job_id)
            document_id = job.document_id if job is not None and job.status == JobStatus.queued else None
        if document_id is None:
            logger.warning('ingest job skipped job_id=%s', job_id)
            return
        # This session stays open, idle, for the whole job: closing it releases the lock.
        with self.session_factory() as lock:
            if not try_lock_document(lock, document_id):
                logger.warning('ingest job deferred, document busy job_id=%s document_id=%s', job_id, document_id)
                return
            self._run_locked(job_id, document_id)

    def _claim(self, job_id: uuid.UUID, progress: Progress) -> tuple[str, str] | None:
        """queued -> running under a row lock. Returns (object_key, filename), or None if the job is not queued."""
        with self.session_factory() as session:
            job = session.scalar(select(IngestJob).where(IngestJob.id == job_id).with_for_update())
            if job is None or job.status != JobStatus.queued:
                return None
            document = job.document
            job.status, job.step, job.started_at = JobStatus.running, 'download', datetime.now(timezone.utc)
            document.status = DocumentStatus.processing
            # Keeps earlier notes, e.g. that a worker died while processing this job.
            progress.warnings.extend(job.warnings or [])
            session.commit()
            return document.object_key, document.filename

    def _run_locked(self, job_id: uuid.UUID, document_id: uuid.UUID) -> None:
        progress = Progress(self.session_factory, job_id)
        started = time.perf_counter()
        try:
            claimed = self._claim(job_id, progress)
            if claimed is None:
                logger.warning('ingest job skipped job_id=%s', job_id)
                return
            object_key, filename = claimed
            progress('download')
            # Re-index jobs replace the previous vectors; for a new document this is a no-op.
            self.ingestion.store.delete_document(str(document_id))
            data = self.storage.read(object_key)
            processed = self.ingestion.process(data, filename, str(document_id), progress=progress,
                                               warn=progress.warn)
            progress('index')
            with self.session_factory() as session:
                document = session.get(Document, document_id)
                if document is None:
                    raise PipelineError('document_deleted', 'Tài liệu đã bị xóa trong lúc xử lý.', 409)
                DocumentRepository(session).replace_chunks(document_id, [
                    Chunk(id=uuid.UUID(chunk_id), document_id=document_id, ordinal=chunk.chunk_index,
                          page=chunk.metadata.get('page'), sheet=chunk.metadata.get('sheet'), content=chunk.content,
                          char_start=chunk.char_start, char_end=chunk.char_end, bboxes=chunk.bboxes)
                    for chunk_id, chunk in zip(processed.chunk_ids, processed.chunks)])
                document.page_count, document.chunk_count = processed.page_count, len(processed.chunks)
                document.status, document.error_message = DocumentStatus.ready, None
                job = session.get(IngestJob, job_id)
                job.status, job.step, job.progress = JobStatus.succeeded, 'done', 100
                job.warnings = progress.warnings or None
                job.timings = progress.finish()
                job.finished_at = datetime.now(timezone.utc)
                session.commit()
            logger.info('ingest done job_id=%s chunks=%d seconds=%.1f warnings=%d', job_id, len(processed.chunks),
                        time.perf_counter() - started, len(progress.warnings))
        except Exception as exc:
            self._fail(job_id, document_id, exc, progress)
            if not isinstance(exc, PipelineError):
                raise

    def _fail(self, job_id: uuid.UUID, document_id: uuid.UUID, exc: Exception, progress: Progress) -> None:
        code = getattr(exc, 'code', 'internal_error')
        message = str(exc) if isinstance(exc, PipelineError) else 'Lỗi hệ thống khi xử lý tài liệu; xem log worker.'
        logger.error('ingest failed job_id=%s code=%s', job_id, code)
        try:
            self.ingestion.store.delete_document(str(document_id))
        except Exception:
            logger.warning('vector cleanup failed job_id=%s', job_id)
        try:
            with self.session_factory() as session:
                job = session.get(IngestJob, job_id)
                if job is not None:
                    mark_failed(job, code, message)
                    job.warnings = progress.warnings or None
                    job.timings = progress.finish()
                session.commit()
        except Exception:
            # The job stays unfinished; the worker's recovery retries it once the database is reachable again.
            logger.exception('cannot record ingest failure job_id=%s', job_id)
