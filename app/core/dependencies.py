from threading import Lock
from functools import lru_cache

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.services.documents import DocumentService
from app.services.embedding import EmbeddingService
from app.services.ingestion import IngestionService
from app.services.ocr_service import OCRService
from app.services.retrieval import RetrievalService
from app.services.vector_store import VectorStore
from app.services.generation import GenerationService
from app.storage.s3 import S3Storage

_lock = Lock()
_services = None


def get_services():
    """Process-wide (ingestion, retrieval) sharing one embedding model and one vector store."""
    global _services
    with _lock:
        if _services is None:
            settings = get_settings()
            embedding = EmbeddingService(settings.embedding_model)
            store = VectorStore.from_settings(settings)
            _services = (
                IngestionService(settings, OCRService(settings.ocr_lang), embedding, store),
                RetrievalService(settings, embedding, store),
            )
        return _services


@lru_cache
def get_storage() -> S3Storage:
    return S3Storage.from_settings(get_settings())


def get_document_service(session: Session = Depends(get_db)) -> DocumentService:
    ingestion, _ = get_services()
    return DocumentService(session, get_storage(), ingestion, ingestion.store)


@lru_cache
def get_generation():
    return GenerationService(get_settings())
