from threading import Lock
import numpy as np
from app.core.errors import PipelineError


class EmbeddingService:
    def __init__(self, model_name: str):
        self.model_name = model_name
        self._model = None
        self._lock = Lock()

    @property
    def model(self):
        with self._lock:
            if self._model is None:
                try:
                    from sentence_transformers import SentenceTransformer
                    self._model = SentenceTransformer(self.model_name, device='cpu')
                except Exception as exc:
                    raise PipelineError('embedding_failure', 'Cannot load embedding model; check server logs.', 503) from exc
            return self._model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            vectors = np.asarray(self.model.encode(texts, normalize_embeddings=True, show_progress_bar=False,
                                                   batch_size=32))
            if (vectors.ndim != 2 or vectors.shape[0] != len(texts) or vectors.shape[1] == 0
                    or not np.isfinite(vectors).all() or np.any(np.linalg.norm(vectors, axis=1) == 0)):
                raise PipelineError('embedding_failure', 'Embedding output has invalid shape or values.', 503)
            return vectors.tolist()
        except PipelineError:
            raise
        except Exception as exc:
            raise PipelineError('embedding_failure', 'Embedding inference failed.', 503) from exc

    def embed_query(self, question: str) -> list[float]:
        return self.embed_documents([question])[0]
