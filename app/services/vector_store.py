import hashlib
import json
import logging
from pathlib import Path

from app.core.errors import PipelineError
from app.models.schemas import Chunk, SearchHit

logger = logging.getLogger(__name__)


class VectorStore:
    def __init__(self, path: Path, model_name: str, chunk_size: int, chunk_overlap: int):
        try:
            import chromadb
            from chromadb.config import Settings
            self.path = Path(path).expanduser().resolve()
            self.client = chromadb.PersistentClient(path=str(self.path), settings=Settings(anonymized_telemetry=False))
            # Isolate incompatible embedding/chunk configurations across restarts.
            signature = json.dumps([model_name, chunk_size, chunk_overlap, 'token-offset-v1'])
            name = 'documents_' + hashlib.sha256(signature.encode()).hexdigest()[:16]
            self.collection = self.client.get_or_create_collection(
                name=name, embedding_function=None, metadata={'hnsw:space': 'cosine'},
            )
            logger.info('vector store path=%s collection=%s tenant=%s database=%s count=%d',
                        self.path, self.collection.name, self.client.tenant,
                        self.client.database, self.collection.count())
        except Exception as exc:
            raise PipelineError('vector_store_failure', 'Cannot open persistent vector storage.', 503) from exc

    def add(self, document_id: str, chunks: list[Chunk], embeddings: list[list[float]]):
        if not chunks or len(chunks) != len(embeddings):
            raise PipelineError('embedding_failure', 'Chunk and embedding counts must match and be nonzero.', 503)
        try:
            count_before = self.collection.count()
            batch_size = self.client.get_max_batch_size()
            for start in range(0, len(chunks), batch_size):
                batch = chunks[start:start + batch_size]
                ids = [f'{document_id}_{chunk.chunk_index}' for chunk in batch]
                metadata = [{**{k: v for k, v in chunk.metadata.items() if v is not None},
                             'document_id': document_id} for chunk in batch]
                self.collection.upsert(
                    ids=ids,
                    documents=[chunk.content for chunk in batch],
                    embeddings=embeddings[start:start + batch_size],
                    metadatas=metadata,
                )
                # Do not acknowledge upload until its records can be read back.
                # Count alone is insufficient: uploading the same file is an upsert.
                saved = self.collection.get(ids=ids, include=['documents', 'metadatas'])
                records = {key: (text, meta) for key, text, meta in
                           zip(saved['ids'], saved['documents'], saved['metadatas'])}
                if any(records.get(key) != (chunk.content, meta)
                       for key, chunk, meta in zip(ids, batch, metadata)):
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

    def search(self, embedding: list[float], top_k: int) -> list[SearchHit]:
        try:
            count = self.collection.count()
            if count == 0:
                return []
            result = self.collection.query(query_embeddings=[embedding], n_results=min(top_k, count),
                                           include=['documents', 'metadatas', 'distances'])
            return [SearchHit(content=text, metadata=meta, distance=distance,
                              relevance=max(-1.0, min(1.0, 1.0 - distance)))
                    for text, meta, distance in zip(result['documents'][0], result['metadatas'][0],
                                                    result['distances'][0])]
        except Exception as exc:
            raise PipelineError('vector_store_failure', 'Similarity search failed.', 503) from exc
