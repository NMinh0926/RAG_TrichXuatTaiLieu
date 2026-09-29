"""Background ingest: the API enqueues a job id on Redis (RQ); the worker runs IngestRunner.run(job_id).

Progress (percent + current step) and non-fatal per-page warnings are written to `ingest_jobs`,
so any API process can report them (GET /api/jobs/{id}, SSE /api/jobs/{id}/events).
"""
import logging
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy.orm import Session

from app.core.errors import PipelineError
from app.db.models import Chunk, Document, DocumentStatus, IngestJob, JobStatus
from app.db.repositories import DocumentRepository
from app.services.ingestion import IngestionService
from app.storage.base import Storage

logger = logging.getLogger(__name__)
# Progress bands per step (percent at step start); extraction and embedding advance inside their band.
STEPS = {'queued': 0, 'download': 2, 'extract': 5, 'chunk': 60, 'embed': 62, 'index': 96, 'done': 100}


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
        """Process one job. Failures mark job and document failed and leave no vectors behind."""
        with self.session_factory() as session:
            job = session.get(IngestJob, job_id)
            if job is None or job.status not in (JobStatus.queued, JobStatus.running):
                logger.warning('ingest job skipped job_id=%s', job_id)
                return
            document = job.document
            document_id, object_key, filename = document.id, document.object_key, document.filename
            job.status, job.step, job.started_at = JobStatus.running, 'download', datetime.now(timezone.utc)
            document.status = DocumentStatus.processing
            session.commit()
        progress = Progress(self.session_factory, job_id)
        started = time.perf_counter()
        try:
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
        with self.session_factory() as session:
            job = session.get(IngestJob, job_id)
            if job is not None:
                job.status, job.error_code, job.error_message = JobStatus.failed, code, message
                job.warnings = progress.warnings or None
                job.timings = progress.finish()
                job.finished_at = datetime.now(timezone.utc)
            document = session.get(Document, document_id)
            if document is not None:
                document.status, document.error_message = DocumentStatus.failed, message
            session.commit()
