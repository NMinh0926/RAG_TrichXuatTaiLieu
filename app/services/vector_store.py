import hashlib
import json
import logging
from pathlib import Path

from app.core.errors import PipelineError
from app.models.schemas import Chunk, SearchHit

logger = logging.getLogger(__name__)


class VectorStore:
    """Chroma collection per embedding/chunk configuration.

    Server mode (`host` set, used by compose so API and worker share one store) or a local
    persistent directory (tests, scripts, running without Docker).
    """

    def __init__(self, path: Path | None, model_name: str, chunk_size: int, chunk_overlap: int,
                 host: str = '', port: int = 8000):
        try:
            import chromadb
            from chromadb.config import Settings
            chroma_settings = Settings(anonymized_telemetry=False)
            if host:
                self.location = f'http://{host}:{port}'
                self.client = chromadb.HttpClient(host=host, port=port, settings=chroma_settings)
            else:
                self.location = str(Path(path).expanduser().resolve())
                self.client = chromadb.PersistentClient(path=self.location, settings=chroma_settings)
            # Isolate incompatible embedding/chunk configurations across restarts.
            signature = json.dumps([model_name, chunk_size, chunk_overlap, 'token-offset-v1'])
            name = 'documents_' + hashlib.sha256(signature.encode()).hexdigest()[:16]
            self.collection = self.client.get_or_create_collection(
                name=name, embedding_function=None, metadata={'hnsw:space': 'cosine'},
            )
            logger.info('vector store location=%s collection=%s count=%d',
                        self.location, self.collection.name, self.collection.count())
        except Exception as exc:
            raise PipelineError('vector_store_failure', 'Cannot open vector storage.', 503) from exc

    @classmethod
    def from_settings(cls, settings) -> 'VectorStore':
        return cls(settings.chroma_path, settings.embedding_model, settings.chunk_size, settings.chunk_overlap,
                   host=settings.chroma_host, port=settings.chroma_port)

    def add(self, document_id: str, chunks: list[Chunk], embeddings: list[list[float]],
            ids: list[str] | None = None):
        """Upsert and read back. `ids` default to '<document_id>_<chunk_index>' (scripts without a database)."""
        if not chunks or len(chunks) != len(embeddings) or (ids is not None and len(ids) != len(chunks)):
            raise PipelineError('embedding_failure', 'Chunk and embedding counts must match and be nonzero.', 503)
        ids = ids or [f'{document_id}_{chunk.chunk_index}' for chunk in chunks]
        try:
            count_before = self.collection.count()
            batch_size = self.client.get_max_batch_size()
            for start in range(0, len(chunks), batch_size):
                batch = chunks[start:start + batch_size]
                batch_ids = ids[start:start + batch_size]
                metadata = [{**{k: v for k, v in chunk.metadata.items() if v is not None},
                             'document_id': document_id} for chunk in batch]
                self.collection.upsert(
                    ids=batch_ids,
                    documents=[chunk.content for chunk in batch],
                    embeddings=embeddings[start:start + batch_size],
                    metadatas=metadata,
                )
                # Do not acknowledge upload until its records can be read back.
                # Count alone is insufficient: uploading the same file is an upsert.
                saved = self.collection.get(ids=batch_ids, include=['documents', 'metadatas'])
                records = {key: (text, meta) for key, text, meta in
                           zip(saved['ids'], saved['documents'], saved['metadatas'])}
                if any(records.get(key) != (chunk.content, meta)
                       for key, chunk, meta in zip(batch_ids, batch, metadata)):
                    raise ValueError('Vector write verification failed')
            logger.info('vector write verified collection=%s embeddings=%d count_before=%d count_after=%d',
                        self.collection.name, len(embeddings), count_before, self.collection.count())
        except Exception as exc:
            # Avoid reporting success with a partially indexed document.
            try:
                self.collection.delete(where={'document_id': document_id})
            except Exception:
                pass
            raise PipelineError('vector_store_failure', 'Cannot persist document chunks.', 503) from exc

    def delete_document(self, document_id: str) -> None:
        try:
            self.collection.delete(where={'document_id': document_id})
        except Exception as exc:
            raise PipelineError('vector_store_failure', 'Cannot delete document vectors.', 503) from exc

    def search(self, embedding: list[float], top_k: int, document_ids: list[str] | None = None) -> list[SearchHit]:
        """Top-K by cosine similarity, optionally restricted to some documents (an empty list matches nothing)."""
        try:
            count = self.collection.count()
            if count == 0 or document_ids == []:
                return []
            where = None
            if document_ids:
                where = ({'document_id': document_ids[0]} if len(document_ids) == 1
                         else {'document_id': {'$in': document_ids}})
            result = self.collection.query(query_embeddings=[embedding], n_results=min(top_k, count), where=where,
                                           include=['documents', 'metadatas', 'distances'])
            return [SearchHit(id=key, content=text, metadata=meta, distance=distance,
                              relevance=max(-1.0, min(1.0, 1.0 - distance)))
                    for key, text, meta, distance in zip(result['ids'][0], result['documents'][0],
                                                         result['metadatas'][0], result['distances'][0])]
        except Exception as exc:
            raise PipelineError('vector_store_failure', 'Similarity search failed.', 503) from exc

    def ping(self) -> None:
        try:
            self.collection.count()
        except Exception as exc:
            raise PipelineError('vector_store_failure', 'Vector storage is unavailable.', 503) from exc
