"""Document lifecycle: object storage + PostgreSQL metadata + Chroma vectors kept consistent."""
import hashlib
import logging
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import PipelineError
from app.db.models import Chunk, Document, DocumentStatus
from app.db.repositories import DocumentRepository
from app.services.file_router import content_type_for, extension_of
from app.services.ingestion import IngestionService
from app.services.vector_store import VectorStore
from app.storage.base import Storage

logger = logging.getLogger(__name__)


class DocumentService:
    def __init__(self, session: Session, storage: Storage, ingestion: IngestionService, store: VectorStore):
        self.session, self.storage, self.ingestion, self.store = session, storage, ingestion, store
        self.repository = DocumentRepository(session)

    def upload(self, filename: str, data: bytes) -> tuple[Document, bool]:
        """Store and index a file. Returns (document, duplicate); an identical file is never reprocessed.

        Failure leaves nothing behind: no object, no rows, no vectors.
        """
        content_type = content_type_for(filename)
        if not data:
            raise PipelineError('empty_document', 'File is missing or empty.')
        sha256 = hashlib.sha256(data).hexdigest()
        existing = self.repository.get_by_sha256(sha256)
        if existing is not None and existing.status != DocumentStatus.failed:
            logger.info('duplicate upload document_id=%s', existing.id)
            return existing, True
        if existing is not None:
            self.delete(existing)
        document_id = uuid.uuid4()
        document = Document(id=document_id, filename=filename, content_type=content_type, size_bytes=len(data),
                            sha256=sha256, object_key=f'documents/{document_id}{extension_of(filename)}',
                            status=DocumentStatus.processing)
        try:
            # Claim the SHA-256 first: a concurrent identical upload loses here, not after OCR.
            self.repository.add(document)
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            winner = self.repository.get_by_sha256(sha256)
            if winner is None:
                raise
            return winner, True
        try:
            self.storage.put(document.object_key, data, content_type)
            processed = self.ingestion.process(data, filename, str(document_id))
            self.repository.replace_chunks(document_id, [
                Chunk(id=uuid.UUID(chunk_id), document_id=document_id, ordinal=chunk.chunk_index,
                      page=chunk.metadata.get('page'), sheet=chunk.metadata.get('sheet'), content=chunk.content,
                      char_start=chunk.char_start, char_end=chunk.char_end)
                for chunk_id, chunk in zip(processed.chunk_ids, processed.chunks)])
            document.page_count = processed.page_count
            document.chunk_count = len(processed.chunks)
            document.status = DocumentStatus.ready
            self.session.commit()
        except Exception:
            self.session.rollback()
            self._cleanup(document_id, document.object_key)
            raise
        logger.info('document stored document_id=%s chunks=%d', document_id, document.chunk_count)
        return document, False

    def delete(self, document: Document) -> None:
        """Vectors first: a failure then leaves a visible document the user can delete again."""
        self.store.delete_document(str(document.id))
        self.storage.delete(document.object_key)
        self.repository.delete(document)
        self.session.commit()
        logger.info('document deleted document_id=%s', document.id)

    def _cleanup(self, document_id: uuid.UUID, object_key: str) -> None:
        for action in (lambda: self.store.delete_document(str(document_id)),
                       lambda: self.storage.delete(object_key)):
            try:
                action()
            except Exception:
                logger.warning('cleanup step failed document_id=%s', document_id)
        try:
            stale = self.repository.get(document_id)
            if stale is not None:
                self.repository.delete(stale)
                self.session.commit()
        except Exception:
            self.session.rollback()
            logger.warning('cleanup of document row failed document_id=%s', document_id)
