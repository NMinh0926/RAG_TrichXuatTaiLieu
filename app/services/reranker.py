"""Cross-encoder reranker (e.g. BAAI/bge-reranker-v2-m3), loaded once and lazily."""
from threading import Lock

from app.core.errors import PipelineError
from app.models.schemas import SearchHit


class Reranker:
    def __init__(self, model_name: str, max_length: int = 512):
        self.model_name, self.max_length = model_name, max_length
        self._model = None
        self._lock = Lock()

    @property
    def model(self):
        with self._lock:
            if self._model is None:
                try:
                    from sentence_transformers import CrossEncoder
                    self._model = CrossEncoder(self.model_name, max_length=self.max_length, device='cpu')
                except Exception as exc:
                    raise PipelineError('reranker_failure', 'Cannot load reranker model; check server logs.', 503) from exc
            return self._model

    def rerank(self, question: str, hits: list[SearchHit]) -> list[SearchHit]:
        """Hits ordered by cross-encoder relevance (stable for ties)."""
        try:
            scores = self.model.predict([(question, hit.content) for hit in hits], show_progress_bar=False)
        except PipelineError:
            raise
        except Exception as exc:
            raise PipelineError('reranker_failure', 'Reranking failed.', 503) from exc
        order = sorted(range(len(hits)), key=lambda i: -float(scores[i]))
        return [hits[i] for i in order]
