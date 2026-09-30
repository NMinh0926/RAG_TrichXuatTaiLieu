"""Real extraction/OCR/embedding/Chroma through APIs; Ollama HTTP is mocked."""
import json
import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.api import questions
from app.core.config import Settings
from app.core.dependencies import get_document_service, get_generation
from sqlalchemy.orm import sessionmaker

from app.services.documents import DocumentService
from app.services.ingestion import IngestionService
from app.services.jobs import IngestRunner, InlineJobQueue
from app.services.retrieval import RetrievalService
from app.services.vector_store import VectorStore
from app.llm.prompts import INSUFFICIENT_ANSWER
from tests.test_generation import grounded_reply, service_with_reply

pytestmark = pytest.mark.integration


@pytest.mark.parametrize('filename,question,keyword,source', [
    ('sample.pdf', 'How many days of annual leave do employees receive?', '12', {'page': 1}),
    ('sample.docx', 'Một năm người lao động có bao nhiêu ngày phép?', '12', {}),
    ('sample.xlsx', 'What is the revenue for Laptop?', '150', {'sheet': 'Sales'}),
    ('sample.jpg', 'How many days of annual leave do employees receive?', '12', {}),
    ('sample_scan.pdf', 'How many days of annual leave do employees receive?', '12', {'page': 1}),
])
def test_upload_then_ask(samples, tmp_path, real_embedding, real_ocr, monkeypatch, db_session, db_engine, db_user,
                         memory_storage, filename, question, keyword, source):
    settings = Settings(chroma_path=tmp_path / 'chroma', chroma_host='', _env_file=None)
    store = VectorStore(settings.chroma_path, settings.embedding_model, settings.chunk_size, settings.chunk_overlap)
    services = (IngestionService(settings, real_ocr, real_embedding, store),
                RetrievalService(settings, real_embedding, store))
    runner = IngestRunner(sessionmaker(bind=db_engine, expire_on_commit=False), memory_storage, services[0])
    app.dependency_overrides[get_document_service] = lambda: DocumentService(db_session, memory_storage, store,
                                                                             InlineJobQueue(runner))
    monkeypatch.setattr(questions, 'get_services', lambda: services)
    # Raw Top-K reaches the model; the fake model cites the chunk containing the keyword.
    generation, fake = service_with_reply(grounded_reply(question, keyword))
    app.dependency_overrides[get_generation] = lambda: generation
    try:
        with TestClient(app) as client:
            count_before = store.collection.count()
            uploaded = client.post('/api/documents/upload', files={'file': (filename, (samples / filename).read_bytes())})
            assert uploaded.status_code == 200, uploaded.text
            assert uploaded.json()['chunks_created'] > 0
            assert uploaded.json()['status'] == 'success'
            assert store.collection.count() == count_before + uploaded.json()['chunks_created']
            saved = store.collection.get(where={'filename': filename}, include=['documents', 'metadatas'])
            assert any(keyword in text for text in saved['documents'])
            raw = store.search(real_embedding.embed_query(question), 10)
            assert any(keyword in hit.content and hit.metadata['filename'] == filename for hit in raw)
            searched = client.post('/questions/search', json={'question': question})
            assert searched.status_code == 200
            assert any(keyword in hit['content'] and hit['metadata']['filename'] == filename
                       for hit in searched.json()['results'])
            response = client.post('/api/questions/ask', json={'question': question})
            assert response.status_code == 200, response.text
            assert keyword in response.json()['answer']
            assert {'filename': filename, **source} in response.json()['sources']
            assert len(fake.chats) == 1
            outside = client.post('/api/questions/ask', json={'question': 'CEO của Microsoft là ai?'})
            assert outside.json() == {'answer': INSUFFICIENT_ANSWER, 'sources': []}
            assert len(fake.chats) == 2
    finally:
        app.dependency_overrides.clear()


def test_persistence_after_process_restart(samples, tmp_path, db_session):
    """Upload in one process, ask in a new one: file, rows and vectors come back from storage.

    Uses scratch resources (test database, scratch bucket, local Chroma dir) via scripts.isolation.
    """
    code = '''
import json, sys
from pathlib import Path
from scripts.isolation import isolate, prepare
isolate(Path(sys.argv[2]))
prepare()
from fastapi.testclient import TestClient
from app.main import app
from app.core.dependencies import get_generation
from tests.test_generation import service_with_reply, user_message
service, fake = service_with_reply('12 days [1]')
app.dependency_overrides[get_generation] = lambda: service
with TestClient(app) as client:
    if sys.argv[1] == 'upload':
        # The scratch database outlives test runs: start from an empty library.
        for item in client.get('/api/documents', params={'page_size': 100}).json()['items']:
            client.delete(f"/api/documents/{item['id']}")
        with open(sys.argv[3], 'rb') as file:
            response = client.post('/api/documents/upload', files={'file': ('sample.pdf', file)})
    else:
        assert client.get(f'/api/documents/{sys.argv[3]}/file').status_code == 200
        response = client.post('/api/questions/ask', json={'question': 'How many days of annual leave do employees receive?'})
        assert '12 days of annual leave' in user_message(fake.chats[0])
    assert response.status_code == 200, response.text
    print(json.dumps(response.json()))
'''

    def run(*args: str) -> dict:
        done = subprocess.run([sys.executable, '-c', code, *args], check=True, capture_output=True, text=True,
                              env=dict(os.environ), timeout=300)
        return json.loads(done.stdout.strip().splitlines()[-1])
    uploaded = run('upload', str(tmp_path), str(samples / 'sample.pdf'))
    response = run('ask', str(tmp_path), uploaded['document_id'])
    assert response['answer'] == '12 days [1]'
    assert response['sources'] == [{'filename': 'sample.pdf', 'page': 1}]
