"""Document lifecycle: object storage + PostgreSQL metadata + Chroma vectors, processed by background jobs."""
import hashlib
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import anyio
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import PipelineError
from app.db.models import Document, DocumentStatus, IngestJob, JobStatus
from app.db.repositories import DocumentRepository
from app.services.file_router import content_type_for, extension_of
from app.services.jobs import JobQueue, locked_documents, mark_failed
from app.services.vector_store import VectorStore
from app.storage.base import Storage

logger = logging.getLogger(__name__)
# HTTP status for job failures reported through the synchronous (legacy) upload endpoint.
FAILURE_STATUS = {'unsupported_file': 415, 'file_too_large': 413, 'empty_document': 422,
                  'extraction_failure': 422, 'no_usable_text': 422}


@dataclass
class Submission:
    document: Document
    job: IngestJob | None
    duplicate: bool


class DocumentService:
    def __init__(self, session: Session, storage: Storage, store: VectorStore, queue: JobQueue,
                 stale_after_seconds: float | None = None):
        """`stale_after_seconds` (default INGEST_JOB_TIMEOUT_SECONDS): a job queued longer than this that no
        worker has started no longer blocks deleting or re-indexing its document."""
        self.session, self.storage, self.store, self.queue = session, storage, store, queue
        self.repository = DocumentRepository(session)
        self.stale_after_seconds = (get_settings().ingest_job_timeout_seconds if stale_after_seconds is None
                                    else stale_after_seconds)

    def latest_job(self, document_id: uuid.UUID) -> IngestJob | None:
        return self.session.scalar(select(IngestJob).where(IngestJob.document_id == document_id)
                                   .order_by(IngestJob.created_at.desc()).limit(1))

    def unfinished_jobs(self, document_id: uuid.UUID) -> list[IngestJob]:
        return list(self.session.scalars(select(IngestJob).where(
            IngestJob.document_id == document_id, IngestJob.status.in_([JobStatus.queued, JobStatus.running]))))

    def busy(self, document: Document) -> bool:
        """Whether the document is really in flight: a worker is processing it (holds its lock) or a job was queued
        recently. A job left behind by a crashed worker, a lost queue entry or a database outage does not count, so
        such a document can still be deleted or re-indexed."""
        if document.status not in (DocumentStatus.pending, DocumentStatus.processing):
            return False
        if locked_documents(self.session, [document.id]):
            return True
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=self.stale_after_seconds)
        return any(job.status == JobStatus.queued and job.created_at > cutoff
                   for job in self.unfinished_jobs(document.id))

    def submit(self, filename: str, data: bytes, owner_id: uuid.UUID | None = None) -> Submission:
        """Store the file and queue its processing. An identical file (same SHA-256) of the same owner is never
        processed twice: a ready or in-flight document is returned as a duplicate; a failed one is replaced.
        Documents without an owner (maintenance scripts) are shared with every user."""
        content_type = content_type_for(filename)
        if not data:
            raise PipelineError('empty_document', 'File is missing or empty.')
        sha256 = hashlib.sha256(data).hexdigest()
        existing = self.repository.get_by_sha256(sha256, owner_id)
        if existing is not None and existing.status != DocumentStatus.failed:
            logger.info('duplicate upload document_id=%s status=%s', existing.id, existing.status.value)
            return Submission(existing, self.latest_job(existing.id), True)
        if existing is not None:
            self.delete(existing)
        document_id = uuid.uuid4()
        object_key = f'documents/{document_id}{extension_of(filename)}'
        document = Document(id=document_id, filename=filename, content_type=content_type, size_bytes=len(data),
                            sha256=sha256, object_key=object_key, status=DocumentStatus.pending,
                            owner_id=owner_id, is_shared=owner_id is None)
        job = IngestJob(document_id=document_id)
        try:
            # Claim the SHA-256 before storing anything: a concurrent identical upload loses here.
            self.repository.add(document)
            self.session.add(job)
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            winner = self.repository.get_by_sha256(sha256, owner_id)
            if winner is None:
                raise
            return Submission(winner, self.latest_job(winner.id), True)
        try:
            self.storage.put(object_key, data, content_type)
            self.queue.enqueue(job.id)
        except Exception:
            self.session.rollback()
            self._discard(document_id, object_key)
            raise
        logger.info('document queued document_id=%s job_id=%s bytes=%d', document_id, job.id, len(data))
        return Submission(document, job, False)

    def reindex(self, document: Document) -> IngestJob:
        """Queue re-extraction/re-embedding of a stored file with the current configuration.

        Unfinished jobs of a stuck document (see `busy`) are failed first, so the worker never runs them. If the
        queue is unavailable, the new job is removed and the document keeps its previous state.
        """
        if self.busy(document):
            raise PipelineError('document_processing', 'Tài liệu đang được xử lý.', 409)
        for stale in self.unfinished_jobs(document.id):
            mark_failed(stale, 'job_abandoned', 'Tác vụ xử lý bị bỏ dở (worker hoặc hàng đợi gặp sự cố).')
        previous = document.status, document.error_message
        job = IngestJob(document_id=document.id)
        document.status, document.error_message = DocumentStatus.pending, None
        self.session.add(job)
        self.session.commit()
        try:
            self.queue.enqueue(job.id)
        except Exception:
            self.session.rollback()
            self.session.refresh(job)
            if job.status == JobStatus.queued:  # never started (an inline job may already have failed)
                self.session.delete(job)
                self.session.refresh(document)
                document.status, document.error_message = previous
                self.session.commit()
            raise
        return job

    async def wait(self, job_id: uuid.UUID, timeout_seconds: float,
                   disconnected: Callable[[], Awaitable[bool]] | None = None, poll_seconds: float = 1.0) -> IngestJob:
        """Wait until the job finishes (synchronous legacy upload endpoint).

        Async: between polls it holds neither a worker thread nor a database connection, and it stops as soon as
        `disconnected()` reports that the client went away.
        """
        deadline = time.monotonic() + timeout_seconds
        while True:
            job = await anyio.to_thread.run_sync(self._poll, job_id)
            if job.status in (JobStatus.succeeded, JobStatus.failed):
                return job
            if time.monotonic() > deadline:
                raise PipelineError('ingest_timeout', 'Xử lý tài liệu quá thời gian chờ; tài liệu vẫn đang được xử lý.', 504)
            if disconnected is not None and await disconnected():
                raise PipelineError('client_disconnected', 'Kết nối đã đóng; tài liệu vẫn đang được xử lý.', 499)
            await anyio.sleep(poll_seconds)

    def _poll(self, job_id: uuid.UUID) -> IngestJob:
        self.session.expire_all()
        job = self.session.get(IngestJob, job_id)
        # Ends the read transaction, which returns the connection to the pool until the next poll.
        self.session.commit()
        if job is None:
            raise PipelineError('job_not_found', 'Không tìm thấy tác vụ xử lý.', 404)
        return job

    def raise_for_failure(self, job: IngestJob) -> None:
        if job.status == JobStatus.failed:
            code = job.error_code or 'ingest_failure'
            raise PipelineError(code, job.error_message or 'Xử lý tài liệu thất bại.', FAILURE_STATUS.get(code, 503))

    def delete(self, document: Document, force: bool = False) -> None:
        """Vectors first: a failure then leaves a visible document the user can delete again.

        In-flight documents (see `busy`) are refused unless `force` (maintenance scripts, no worker running on
        them). Deleting one whose job is still queued is safe: the job row goes with it and the worker skips it.
        """
        if not force and self.busy(document):
            raise PipelineError('document_processing', 'Tài liệu đang được xử lý; hãy xóa sau khi xử lý xong.', 409)
        self.store.delete_document(str(document.id))
        self.storage.delete(document.object_key)
        self.repository.delete(document)
        self.session.commit()
        logger.info('document deleted document_id=%s', document.id)

    def _discard(self, document_id: uuid.UUID, object_key: str) -> None:
        """Undo a submission whose file could not be stored or queued."""
        try:
            self.storage.delete(object_key)
        except Exception:
            logger.warning('object cleanup failed document_id=%s', document_id)
        try:
            row = self.repository.get(document_id)
            if row is not None:
                self.repository.delete(row)
                self.session.commit()
        except Exception:
            self.session.rollback()
            logger.warning('cleanup of document row failed document_id=%s', document_id)
