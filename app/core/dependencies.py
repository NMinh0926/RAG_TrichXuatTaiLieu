from threading import Lock
from functools import lru_cache
from app.core.config import get_settings
from app.services.embedding import EmbeddingService
from app.services.ingestion import IngestionService
from app.services.ocr_service import OCRService
from app.services.retrieval import RetrievalService
from app.services.vector_store import VectorStore
from app.services.generation import GenerationService

_lock = Lock()
_services = None


def get_services():
    global _services
    with _lock:
        if _services is None:
            settings = get_settings()
            embedding = EmbeddingService(settings.embedding_model)
            store = VectorStore(settings.chroma_path, settings.embedding_model,
                                settings.chunk_size, settings.chunk_overlap)
            _services = (
                IngestionService(settings, OCRService(settings.ocr_lang), embedding, store),
                RetrievalService(settings, embedding, store),
            )
        return _services


@lru_cache
def get_generation():
    return GenerationService(get_settings())
