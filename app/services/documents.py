"""Document lifecycle: object storage + PostgreSQL metadata + Chroma vectors, processed by background jobs."""
import hashlib
import logging
import time
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import PipelineError
from app.db.models import Document, DocumentStatus, IngestJob, JobStatus
from app.db.repositories import DocumentRepository
from app.services.file_router import content_type_for, extension_of
from app.services.jobs import JobQueue
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
    def __init__(self, session: Session, storage: Storage, store: VectorStore, queue: JobQueue):
        self.session, self.storage, self.store, self.queue = session, storage, store, queue
        self.repository = DocumentRepository(session)

    def latest_job(self, document_id: uuid.UUID) -> IngestJob | None:
        return self.session.scalar(select(IngestJob).where(IngestJob.document_id == document_id)
                                   .order_by(IngestJob.created_at.desc()).limit(1))

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
        """Queue re-extraction/re-embedding of a stored file with the current configuration."""
        if document.status in (DocumentStatus.pending, DocumentStatus.processing):
            raise PipelineError('document_processing', 'Tài liệu đang được xử lý.', 409)
        job = IngestJob(document_id=document.id)
        document.status, document.error_message = DocumentStatus.pending, None
        self.session.add(job)
        self.session.commit()
        self.queue.enqueue(job.id)
        return job

    def wait(self, job_id: uuid.UUID, timeout_seconds: float, poll_seconds: float = 0.5) -> IngestJob:
        """Block until the job finishes (for the synchronous legacy upload endpoint)."""
        deadline = time.monotonic() + timeout_seconds
        while True:
            self.session.expire_all()
            job = self.session.get(IngestJob, job_id)
            if job is None:
                raise PipelineError('job_not_found', 'Không tìm thấy tác vụ xử lý.', 404)
            if job.status in (JobStatus.succeeded, JobStatus.failed):
                return job
            if time.monotonic() > deadline:
                raise PipelineError('ingest_timeout', 'Xử lý tài liệu quá thời gian chờ; tài liệu vẫn đang được xử lý.', 504)
            time.sleep(poll_seconds)

    def raise_for_failure(self, job: IngestJob) -> None:
        if job.status == JobStatus.failed:
            code = job.error_code or 'ingest_failure'
            raise PipelineError(code, job.error_message or 'Xử lý tài liệu thất bại.', FAILURE_STATUS.get(code, 503))

    def delete(self, document: Document, force: bool = False) -> None:
        """Vectors first: a failure then leaves a visible document the user can delete again.

        In-flight documents are refused unless `force` (maintenance scripts, no worker running on them).
        """
        if not force and document.status in (DocumentStatus.pending, DocumentStatus.processing):
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
