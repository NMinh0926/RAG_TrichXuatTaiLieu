from threading import Lock
from functools import lru_cache

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db, get_sessionmaker
from app.llm.client import LLMClient
from app.services.chat import ChatService
from app.services.documents import DocumentService
from app.services.embedding import EmbeddingService
from app.services.ingestion import IngestionService
from app.services.jobs import RQJobQueue
from app.services.ocr_service import OCRService
from app.services.reranker import Reranker
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
            reranker = Reranker(settings.reranker_model) if settings.reranker_model else None
            _services = (
                IngestionService(settings, OCRService(settings.ocr_lang), embedding, store),
                RetrievalService(settings, embedding, store, reranker),
            )
        return _services


@lru_cache
def get_storage() -> S3Storage:
    return S3Storage.from_settings(get_settings())


@lru_cache
def get_job_queue() -> RQJobQueue:
    settings = get_settings()
    return RQJobQueue(settings.redis_url, settings.ingest_job_timeout_seconds)


def get_document_service(session: Session = Depends(get_db)) -> DocumentService:
    _, retrieval = get_services()
    return DocumentService(session, get_storage(), retrieval.store, get_job_queue())


@lru_cache
def get_llm() -> LLMClient:
    return LLMClient(get_settings())


@lru_cache
def get_generation() -> GenerationService:
    return GenerationService(get_settings(), get_llm())


@lru_cache
def get_summary_llm() -> LLMClient | None:
    settings = get_settings()
    return LLMClient(settings, model=settings.llm_summary_model) if settings.llm_summary_model else None


def get_chat_service() -> ChatService:
    # Streaming responses outlive request-scoped dependencies: the service opens its own sessions.
    return ChatService(get_sessionmaker(), get_services()[1], get_generation(), get_llm(), get_settings(),
                       get_summary_llm())
