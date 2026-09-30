"""Production dependency lifecycle with real embeddings/storage; Ollama HTTP explicitly mocked."""
import io
from types import SimpleNamespace

import numpy as np
import pytest
from docx import Document
from fastapi.testclient import TestClient

from app.main import app
from app.core import dependencies
from app.core.config import get_settings
from app.core.dependencies import get_generation
from app.db.session import get_db
from app.llm.prompts import INSUFFICIENT_ANSWER
from tests.test_generation import grounded_reply, service_with_reply, user_message


@pytest.mark.integration
@pytest.mark.parametrize('fact,question,keyword', [
    ('Nhân viên chính thức được nghỉ phép 12 ngày mỗi năm.',
     'Một năm nhân viên được nghỉ bao nhiêu ngày phép?', '12'),
    ('Project Orion có mã nội bộ ZXQ-91827.', 'Mã nội bộ của Project Orion là gì?', 'ZXQ-91827'),
])
def test_upload_is_stored_and_immediately_searchable(tmp_path, monkeypatch, real_embedding, db_session, db_engine, db_user,
                                                    memory_storage, fact, question, keyword):
    from sqlalchemy.orm import sessionmaker
    from app.services.jobs import IngestRunner, InlineJobQueue
    monkeypatch.setenv('CHROMA_HOST', '')
    monkeypatch.setenv('CHROMA_PATH', str(tmp_path / 'chroma'))
    monkeypatch.setattr(dependencies, '_services', None)
    monkeypatch.setattr(dependencies, 'get_storage', lambda: memory_storage)
    # The worker's code path, run inline against the test database.
    monkeypatch.setattr(dependencies, 'get_job_queue', lambda: InlineJobQueue(IngestRunner(
        sessionmaker(bind=db_engine, expire_on_commit=False), memory_storage, dependencies.get_services()[0])))
    app.dependency_overrides[get_db] = lambda: db_session
    # Share the already loaded real model, leaving production service wiring intact.
    monkeypatch.setattr(dependencies, 'EmbeddingService', lambda name: real_embedding)
    get_settings.cache_clear()
    generation, fake = service_with_reply(grounded_reply(question, keyword))
    app.dependency_overrides[get_generation] = lambda: generation
    try:
        ingestion, retrieval = dependencies.get_services()
        assert ingestion.store is retrieval.store
        store = ingestion.store
        assert store.collection.count() == 0
        document = Document()
        document.add_paragraph(fact)
        buffer = io.BytesIO()
        document.save(buffer)
        with TestClient(app) as client:
            uploaded = client.post('/api/documents/upload', files={'file': ('unique.docx', buffer.getvalue())})
            assert uploaded.status_code == 200
            assert uploaded.json()['chunks_created'] > 0
            assert store.collection.count() == uploaded.json()['chunks_created']
            saved = store.collection.get(include=['documents', 'metadatas', 'embeddings'])
            assert any(fact in text for text in saved['documents'])
            vectors = np.asarray(saved['embeddings'])
            query = real_embedding.embed_query(question)
            assert vectors.shape == (uploaded.json()['chunks_created'], len(query))
            assert np.isfinite(vectors).all()
            assert np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-5)
            raw = store.search(query, 10)
            assert keyword in raw[0].content
            assert raw[0].relevance == pytest.approx(1 - raw[0].distance)
            filtered = client.post('/questions/search', json={'question': question})
            assert any(keyword in h['content'] for h in filtered.json()['results'])
            answer = client.post('/api/questions/ask', json={'question': question})
            assert answer.status_code == 200
            assert answer.json()['answer'] == f'{keyword} [1]'
            assert fact in user_message(fake.chats[0])
            assert answer.json()['sources'] == [{'filename': 'unique.docx'}]
            outside = client.post('/api/questions/ask', json={'question': 'CEO của Microsoft là ai?'})
            assert outside.json() == {'answer': INSUFFICIENT_ANSWER, 'sources': []}
            assert len(fake.chats) == 2
    finally:
        app.dependency_overrides.clear()
        get_settings.cache_clear()


def test_silent_vector_write_cannot_report_upload_success(tmp_path, monkeypatch):
    from app.services.vector_store import VectorStore
    from app.models.schemas import Chunk
    from app.core.errors import PipelineError
    store = VectorStore(tmp_path, 'test', 700, 120)
    monkeypatch.setattr(type(store.collection), 'upsert', lambda *args, **kwargs: None)
    with pytest.raises(PipelineError) as caught:
        store.add('test', [Chunk(content='fact', chunk_index=0, metadata={'filename': 'x.docx'})], [[1., 0.]])
    assert caught.value.code == 'vector_store_failure'
    assert store.collection.count() == 0


@pytest.mark.parametrize('vectors', [[], [[float('nan'), 1]], [[float('inf'), 1]], [[0., 0.]], [[]]])
def test_invalid_embeddings_are_rejected(vectors):
    from app.services.embedding import EmbeddingService
    from app.core.errors import PipelineError
    service = EmbeddingService('test')
    service._model = SimpleNamespace(encode=lambda *args, **kwargs: vectors)
    with pytest.raises(PipelineError) as caught:
        service.embed_documents(['fact'])
    assert caught.value.code == 'embedding_failure'
