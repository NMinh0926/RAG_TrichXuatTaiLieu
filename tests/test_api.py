from fastapi.testclient import TestClient
from app.main import app
from app.core.config import Settings, get_settings
from app.api import documents, questions
from app.services.ingestion import IngestionService
from app.services.retrieval import RetrievalService
from app.services.vector_store import VectorStore
from tests.conftest import FakeEmbedding, FakeOCR


def test_health_and_openapi():
    with TestClient(app) as client:
        assert client.get('/health').json()['status'] == 'ok'
        assert '/questions/search' in client.get('/openapi.json').json()['paths']


def test_question_validation():
    with TestClient(app) as client:
        assert client.post('/questions/search', json={'question': '   '}).status_code == 422
        assert client.post('/questions/search', json={'question': 'q', 'top_k': 0}).status_code == 422


def test_upload_errors(tmp_path):
    app.dependency_overrides[get_settings] = lambda: Settings(upload_dir=tmp_path, max_upload_mb=1, _env_file=None)
    try:
        with TestClient(app) as client:
            response = client.post('/documents/ingest', files={'file': ('p.txt', b'bad')})
            assert response.status_code == 415
            response = client.post('/documents/ingest', files={'file': ('p.pdf', b'0' * (1024 * 1024 + 1))})
            assert response.status_code == 413
            assert list(tmp_path.iterdir()) == []
    finally:
        app.dependency_overrides.clear()


def test_upload_search_and_recovery(samples, tmp_path, monkeypatch):
    settings = Settings(upload_dir=tmp_path / 'uploads', chroma_path=tmp_path / 'chroma', _env_file=None)
    store = VectorStore(settings.chroma_path, 'api-test', 700, 120)
    embedding = FakeEmbedding()
    services = (IngestionService(settings, FakeOCR(), embedding, store),
                RetrievalService(settings, embedding, store))
    monkeypatch.setattr(documents, 'get_services', lambda: services)
    monkeypatch.setattr(questions, 'get_services', lambda: services)
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(app) as client:
            bad = client.post('/documents/ingest', files={'file': ('bad.pdf', b'broken')})
            assert bad.status_code == 422
            assert bad.json()['error'] == 'extraction_failure'
            uploaded = client.post('/documents/ingest', files={
                'file': ('../../sample.docx', (samples / 'sample.docx').read_bytes())})
            assert uploaded.status_code == 200
            assert uploaded.json()['filename'] == 'sample.docx'
            hits = client.post('/questions/search', json={'question': 'nghỉ phép'}).json()['results']
            assert hits[0]['metadata']['filename'] == 'sample.docx'
            assert len(list(settings.upload_dir.iterdir())) == 1
            assert client.get('/health').status_code == 200
    finally:
        app.dependency_overrides.clear()
