from collections.abc import Callable
from threading import Lock

import numpy as np

from app.core.errors import PipelineError

BATCH_SIZE = 16


class EmbeddingService:
    """SentenceTransformer loaded once per process; GPU when torch sees one, otherwise CPU."""

    def __init__(self, model_name: str):
        self.model_name = model_name
        self._model = None
        self._lock = Lock()

    @property
    def model(self):
        with self._lock:
            if self._model is None:
                try:
                    import torch
                    from sentence_transformers import SentenceTransformer
                    device = 'cuda' if torch.cuda.is_available() else 'cpu'
                    self._model = SentenceTransformer(self.model_name, device=device)
                except Exception as exc:
                    raise PipelineError('embedding_failure', 'Cannot load embedding model; check server logs.', 503) from exc
            return self._model

    def embed_documents(self, texts: list[str], progress: Callable[[float], None] | None = None) -> list[list[float]]:
        """Normalized vectors, computed in batches; `progress` receives the finished fraction."""
        if not texts:
            return []
        try:
            batches = []
            for start in range(0, len(texts), BATCH_SIZE):
                batches.append(np.asarray(self.model.encode(texts[start:start + BATCH_SIZE], normalize_embeddings=True,
                                                            show_progress_bar=False, batch_size=BATCH_SIZE)))
                if progress is not None:
                    progress(min(1.0, (start + BATCH_SIZE) / len(texts)))
            vectors = np.concatenate(batches)
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
