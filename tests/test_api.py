"""Documents API on the real test database, in-memory object storage and a local Chroma directory."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.api import questions
from app.core.config import Settings, get_settings
from app.core.dependencies import get_document_service
from app.db.models import Document, DocumentStatus
from app.services.documents import DocumentService
from app.services.ingestion import IngestionService
from app.services.retrieval import RetrievalService
from app.services.vector_store import VectorStore
from tests.conftest import FakeEmbedding, FakeOCR


@pytest.fixture
def documents_api(db_session, memory_storage, tmp_path, monkeypatch):
    settings = Settings(chroma_path=tmp_path / 'chroma', chroma_host='', max_upload_mb=1, _env_file=None)
    store = VectorStore(settings.chroma_path, 'api-test', 700, 120)
    embedding = FakeEmbedding()
    ingestion = IngestionService(settings, FakeOCR(), embedding, store)
    monkeypatch.setattr(questions, 'get_services', lambda: (ingestion, RetrievalService(settings, embedding, store)))
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_document_service] = lambda: DocumentService(db_session, memory_storage, ingestion, store)
    try:
        with TestClient(app) as client:
            yield client, store, memory_storage, db_session
    finally:
        app.dependency_overrides.clear()


def upload(client, name, data):
    return client.post('/api/documents/upload', files={'file': (name, data)})


def test_health_and_openapi():
    with TestClient(app) as client:
        assert client.get('/health').json()['status'] == 'ok'
        paths = client.get('/openapi.json').json()['paths']
        assert {'/api/documents', '/api/documents/{document_id}', '/api/documents/{document_id}/file',
                '/api/documents/upload', '/api/questions/ask'} <= set(paths)


def test_question_validation():
    with TestClient(app) as client:
        assert client.post('/questions/search', json={'question': '   '}).status_code == 422
        assert client.post('/questions/search', json={'question': 'q', 'top_k': 0}).status_code == 422


def test_upload_rejects_type_and_size(documents_api):
    client, store, storage, _ = documents_api
    assert upload(client, 'p.txt', b'bad').status_code == 415
    assert upload(client, 'p.pdf', b'0' * (1024 * 1024 + 1)).status_code == 413
    assert storage.objects == {} and store.collection.count() == 0


def test_upload_stores_file_rows_and_vectors(documents_api, samples):
    client, store, storage, session = documents_api
    data = (samples / 'sample.docx').read_bytes()
    response = upload(client, '../../sample.docx', data)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['status'] == 'success' and body['filename'] == 'sample.docx' and body['duplicate'] is False
    document = session.get(Document, uuid.UUID(body['document_id']))
    assert document.status == DocumentStatus.ready
    assert document.chunk_count == body['chunks_created'] > 0
    assert storage.read(document.object_key) == data
    assert document.object_key.startswith(f'documents/{document.id}')
    assert store.collection.count() == document.chunk_count
    vectors = store.collection.get(include=['metadatas'])
    assert {meta['document_id'] for meta in vectors['metadatas']} == {str(document.id)}
    assert set(vectors['ids']) == {str(chunk.id) for chunk in document.chunks}
    assert document.chunks[0].char_end > document.chunks[0].char_start >= 0


def test_duplicate_upload_is_not_reprocessed(documents_api, samples):
    client, store, storage, _ = documents_api
    data = (samples / 'sample.pdf').read_bytes()
    first = upload(client, 'a.pdf', data).json()
    second = upload(client, 'renamed.pdf', data).json()
    assert second['duplicate'] is True
    assert second['document_id'] == first['document_id'] and second['filename'] == 'a.pdf'
    assert len(storage.objects) == 1 and store.collection.count() == first['chunks_created']


def test_failed_upload_leaves_nothing(documents_api):
    client, store, storage, session = documents_api
    response = upload(client, 'broken.pdf', b'not a pdf')
    assert response.status_code == 422 and response.json()['error'] == 'extraction_failure'
    assert storage.objects == {} and store.collection.count() == 0
    assert session.query(Document).count() == 0


def test_list_paginates_and_searches(documents_api, samples):
    client, *_ = documents_api
    for name in ('sample.pdf', 'sample.docx', 'sample.xlsx'):
        assert upload(client, f'Báo cáo {name}', (samples / name).read_bytes()).status_code == 200
    page = client.get('/api/documents', params={'page': 1, 'page_size': 2}).json()
    assert page['total'] == 3 and len(page['items']) == 2 and page['page_size'] == 2
    assert page['items'][0]['filename'] == 'Báo cáo sample.xlsx'  # newest first
    assert len(client.get('/api/documents', params={'page': 2, 'page_size': 2}).json()['items']) == 1
    found = client.get('/api/documents', params={'q': 'DOCX'}).json()
    assert [item['filename'] for item in found['items']] == ['Báo cáo sample.docx']
    assert client.get('/api/documents', params={'q': '100%_'}).json()['total'] == 0
    assert client.get('/api/documents', params={'page_size': 101}).status_code == 422


def test_get_and_stream_file(documents_api, samples):
    client, *_ = documents_api
    data = (samples / 'sample.pdf').read_bytes()
    document_id = upload(client, 'Quy chế.pdf', data).json()['document_id']
    detail = client.get(f'/api/documents/{document_id}').json()
    assert detail['status'] == 'ready' and detail['page_count'] == 1 and detail['content_type'] == 'application/pdf'
    file = client.get(f'/api/documents/{document_id}/file')
    assert file.status_code == 200 and file.content == data
    assert file.headers['content-type'] == 'application/pdf'
    assert file.headers['content-disposition'] == "inline; filename*=UTF-8''Quy%20ch%E1%BA%BF.pdf"
    missing = uuid.uuid4()
    assert client.get(f'/api/documents/{missing}').status_code == 404
    assert client.get(f'/api/documents/{missing}/file').json()['error'] == 'document_not_found'
    assert client.get('/api/documents/not-a-uuid').status_code == 422


def test_delete_removes_file_rows_and_vectors(documents_api, samples):
    client, store, storage, session = documents_api
    keep = upload(client, 'keep.docx', (samples / 'sample.docx').read_bytes()).json()
    gone = upload(client, 'gone.pdf', (samples / 'sample.pdf').read_bytes()).json()
    assert client.delete(f"/api/documents/{gone['document_id']}").status_code == 204
    assert client.get(f"/api/documents/{gone['document_id']}").status_code == 404
    assert client.delete(f"/api/documents/{gone['document_id']}").status_code == 404
    remaining = store.collection.get(include=['metadatas'])['metadatas']
    assert {meta['document_id'] for meta in remaining} == {keep['document_id']}
    assert len(storage.objects) == 1
    assert session.query(Document).count() == 1


def test_search_after_upload(documents_api, samples):
    client, *_ = documents_api
    upload(client, 'sample.docx', (samples / 'sample.docx').read_bytes())
    hits = client.post('/questions/search', json={'question': 'nghỉ phép'}).json()['results']
    assert hits[0]['metadata']['filename'] == 'sample.docx'
    assert hits[0]['id']
