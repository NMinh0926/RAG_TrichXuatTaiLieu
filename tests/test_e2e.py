"""Real extraction/OCR/embedding/Chroma through APIs; Ollama HTTP is mocked."""
import json
import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.api import documents, questions
from app.core.config import Settings, get_settings
from app.core.dependencies import get_generation
from app.services.ingestion import IngestionService
from app.services.retrieval import RetrievalService
from app.services.vector_store import VectorStore
from app.services.prompts import INSUFFICIENT_ANSWER
from tests.test_generation import grounded_reply, service_with_reply

pytestmark = pytest.mark.integration


@pytest.mark.parametrize('filename,question,keyword,source', [
    ('sample.pdf', 'How many days of annual leave do employees receive?', '12', {'page': 1}),
    ('sample.docx', 'Một năm người lao động có bao nhiêu ngày phép?', '12', {}),
    ('sample.xlsx', 'What is the revenue for Laptop?', '150', {'sheet': 'Sales'}),
    ('sample.jpg', 'How many days of annual leave do employees receive?', '12', {}),
    ('sample_scan.pdf', 'How many days of annual leave do employees receive?', '12', {'page': 1}),
])
def test_upload_then_ask(samples, tmp_path, real_embedding, real_ocr, monkeypatch,
                         filename, question, keyword, source):
    settings = Settings(upload_dir=tmp_path / 'uploads', chroma_path=tmp_path / 'chroma', _env_file=None)
    store = VectorStore(settings.chroma_path, settings.embedding_model, settings.chunk_size, settings.chunk_overlap)
    services = (IngestionService(settings, real_ocr, real_embedding, store),
                RetrievalService(settings, real_embedding, store))
    monkeypatch.setattr(documents, 'get_services', lambda: services)
    monkeypatch.setattr(questions, 'get_services', lambda: services)
    # Raw Top-K reaches the model; the fake model cites the chunk containing the keyword.
    generation, fake = service_with_reply(grounded_reply(question, keyword))
    app.dependency_overrides[get_settings] = lambda: settings
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


def test_persistence_after_process_restart(samples, tmp_path):
    code = '''
import json, sys
from fastapi.testclient import TestClient
from app.main import app
from app.core.dependencies import get_generation
from tests.test_generation import service_with_reply
service, _ = service_with_reply('12 days [Nguồn: sample.pdf, trang 1]')
app.dependency_overrides[get_generation] = lambda: service
with TestClient(app) as client:
    if sys.argv[1] == 'upload':
        with open(sys.argv[2], 'rb') as file:
            response = client.post('/api/documents/upload', files={'file': ('sample.pdf', file)})
    else:
        response = client.post('/api/questions/ask', json={'question':'How many days of annual leave do employees receive?'})
    assert response.status_code == 200, response.text
    print(json.dumps(response.json()))
'''
    env = {**os.environ, 'CHROMA_PATH': str(tmp_path / 'chroma'), 'UPLOAD_DIR': str(tmp_path / 'uploads')}
    subprocess.run([sys.executable, '-c', code, 'upload', str(samples / 'sample.pdf')],
                   check=True, capture_output=True, text=True, env=env, timeout=180)
    restarted = subprocess.run([sys.executable, '-c', code, 'ask'], check=True, capture_output=True,
                               text=True, env=env, timeout=180)
    response = json.loads(restarted.stdout.strip().splitlines()[-1])
    assert response['answer'] == '12 days [Nguồn: sample.pdf, trang 1]'
    assert response['sources'] == [{'filename': 'sample.pdf', 'page': 1}]
