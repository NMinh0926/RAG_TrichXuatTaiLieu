"""Documents and jobs API on the real test database, in-memory object storage and a local Chroma directory.

Jobs run inline (InlineJobQueue), exactly the code path of the worker.
"""
import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.api import documents as documents_api_module, questions
from app.core.config import Settings, get_settings
from app.core.dependencies import get_document_service
from app.db.models import Document, DocumentStatus, IngestJob, JobStatus
from app.db.session import get_db
from app.services.documents import DocumentService
from app.services.ingestion import IngestionService
from app.services.jobs import IngestRunner, InlineJobQueue
from app.services.retrieval import RetrievalService
from app.services.vector_store import VectorStore
from tests.conftest import FakeEmbedding, FakeOCR


@pytest.fixture
def api(db_session, db_engine, memory_storage, tmp_path, monkeypatch):
    def make(ocr=None):
        settings = Settings(chroma_path=tmp_path / 'chroma', chroma_host='', max_upload_mb=1, _env_file=None)
        store = VectorStore(settings.chroma_path, 'api-test', settings.chunk_size, settings.chunk_overlap)
        embedding = FakeEmbedding()
        ingestion = IngestionService(settings, ocr or FakeOCR(), embedding, store)
        factory = sessionmaker(bind=db_engine, expire_on_commit=False)
        runner = IngestRunner(factory, memory_storage, ingestion)
        service = DocumentService(db_session, memory_storage, store, InlineJobQueue(runner))
        monkeypatch.setattr(questions, 'get_services', lambda: (ingestion, RetrievalService(settings, embedding, store)))
        monkeypatch.setattr(documents_api_module, 'get_sessionmaker', lambda: factory)
        app.dependency_overrides[get_settings] = lambda: settings
        app.dependency_overrides[get_document_service] = lambda: service
        app.dependency_overrides[get_db] = lambda: db_session
        return TestClient(app), store, memory_storage, db_session, service
    yield make
    app.dependency_overrides.clear()


@pytest.fixture
def documents_api(api):
    return api()[:4]


def upload(client, name, data):
    return client.post('/api/documents/upload', files={'file': (name, data)})


def submit(client, name, data):
    return client.post('/api/documents', files={'file': (name, data)})


def events(client, path) -> list[tuple[str, dict]]:
    found, kind = [], None
    with client.stream('GET', path) as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            if line.startswith('event: '):
                kind = line[7:]
            elif line.startswith('data: '):
                found.append((kind, json.loads(line[6:])))
    return found


def mixed_pdf(samples, tmp_path):
    import fitz
    path = tmp_path / 'mixed.pdf'
    with fitz.open() as mixed:
        for filename in ['sample.pdf', 'sample_scan.pdf']:
            with fitz.open(samples / filename) as source:
                mixed.insert_pdf(source)
        mixed.save(path)
    return path.read_bytes()


def test_health_and_openapi():
    with TestClient(app) as client:
        assert client.get('/health').json()['status'] == 'ok'
        paths = client.get('/openapi.json').json()['paths']
        assert {'/api/documents', '/api/documents/{document_id}', '/api/documents/{document_id}/file',
                '/api/documents/{document_id}/reindex', '/api/documents/upload', '/api/jobs/{job_id}',
                '/api/jobs/{job_id}/events', '/api/questions/ask', '/api/chat'} <= set(paths)


def test_question_validation():
    with TestClient(app) as client:
        assert client.post('/questions/search', json={'question': '   '}).status_code == 422
        assert client.post('/questions/search', json={'question': 'q', 'top_k': 0}).status_code == 422


def test_upload_rejects_type_and_size(documents_api):
    client, store, storage, _ = documents_api
    for send in (upload, submit):
        assert send(client, 'p.txt', b'bad').status_code == 415
        assert send(client, 'p.pdf', b'0' * (1024 * 1024 + 1)).status_code == 413
    assert storage.objects == {} and store.collection.count() == 0


def test_submit_returns_job_and_reports_progress(documents_api, samples):
    client, store, storage, session = documents_api
    data = (samples / 'sample.pdf').read_bytes()
    response = submit(client, 'Quy chế.pdf', data)
    assert response.status_code == 202, response.text
    body = response.json()
    assert body['duplicate'] is False and body['document']['filename'] == 'Quy chế.pdf'
    job_id = body['job']['id']
    job = client.get(f'/api/jobs/{job_id}').json()
    assert (job['status'], job['progress'], job['step']) == ('succeeded', 100, 'done')
    assert job['started_at'] and job['finished_at'] and job['error_code'] is None
    assert {'download', 'extract', 'chunk', 'embed', 'index'} <= set(job['timings'])
    streamed = events(client, f'/api/jobs/{job_id}/events')
    assert streamed[-1][0] == 'done' and streamed[-1][1]['status'] == 'succeeded'
    document = session.get(Document, uuid.UUID(body['document']['id']))
    session.refresh(document)
    assert document.status == DocumentStatus.ready and document.page_count == 1 and document.chunk_count > 0
    assert storage.read(document.object_key) == data
    assert set(store.collection.get()['ids']) == {str(chunk.id) for chunk in document.chunks}
    # Text-layer PDF: every chunk is located on its page in PDF points.
    boxes = document.chunks[0].bboxes
    assert boxes and all(box['page'] == 1 and 0 <= box['x0'] < box['x1'] <= 612 and 0 <= box['y0'] < box['y1'] <= 842
                         for box in boxes)
    detail = client.get(f"/api/documents/{document.id}").json()
    assert detail['latest_job']['id'] == job_id and detail['status'] == 'ready'


def test_legacy_upload_waits_for_the_job(documents_api, samples):
    client, store, storage, session = documents_api
    data = (samples / 'sample.docx').read_bytes()
    response = upload(client, '../../sample.docx', data)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body['status'] == 'success' and body['filename'] == 'sample.docx' and body['duplicate'] is False
    document = session.get(Document, uuid.UUID(body['document_id']))
    assert document.status == DocumentStatus.ready and document.chunk_count == body['chunks_created'] > 0
    assert document.chunks[0].bboxes is None  # DOCX has no page geometry
    assert store.collection.count() == document.chunk_count


def test_duplicate_is_not_reprocessed(documents_api, samples):
    client, store, storage, _ = documents_api
    data = (samples / 'sample.pdf').read_bytes()
    first = submit(client, 'a.pdf', data).json()
    again = submit(client, 'renamed.pdf', data)
    assert again.status_code == 200 and again.json()['duplicate'] is True
    assert again.json()['document']['id'] == first['document']['id']
    assert again.json()['job']['id'] == first['job']['id']
    legacy = upload(client, 'other.pdf', data).json()
    assert legacy['duplicate'] is True and legacy['filename'] == 'a.pdf' and legacy['chunks_created'] > 0
    assert len(storage.objects) == 1 and store.collection.count() == legacy['chunks_created']


def test_in_flight_duplicate_returns_the_running_job(documents_api, samples):
    import hashlib
    client, _, _, session = documents_api
    data = (samples / 'sample.pdf').read_bytes()
    document = Document(filename='busy.pdf', content_type='application/pdf', size_bytes=len(data),
                        sha256=hashlib.sha256(data).hexdigest(), object_key='documents/busy.pdf',
                        status=DocumentStatus.processing)
    session.add(document)
    session.flush()
    job = IngestJob(document_id=document.id, status=JobStatus.running, progress=40, step='extract')
    session.add(job)
    session.commit()
    body = submit(client, 'again.pdf', data).json()
    assert body['duplicate'] is True and body['job']['id'] == str(job.id) and body['job']['progress'] == 40
    assert client.delete(f'/api/documents/{document.id}').status_code == 409  # still processing


def test_failed_job_marks_document_failed_and_retry_replaces_it(documents_api):
    client, store, storage, session = documents_api
    response = upload(client, 'broken.pdf', b'not a pdf')
    assert response.status_code == 422 and response.json()['error'] == 'extraction_failure'
    failed = session.query(Document).one()
    assert failed.status == DocumentStatus.failed and failed.error_message
    job = session.query(IngestJob).one()
    assert job.status == JobStatus.failed and job.error_code == 'extraction_failure' and job.finished_at
    assert store.collection.count() == 0 and list(storage.objects) == [failed.object_key]
    retry = submit(client, 'broken.pdf', b'not a pdf').json()
    assert retry['duplicate'] is False and retry['document']['id'] != str(failed.id)
    assert retry['job']['status'] == 'failed' and session.query(Document).count() == 1


def test_one_failing_ocr_page_is_a_warning(api, samples, tmp_path):
    client, store, _, session, _ = api(FakeOCR(fail_calls={1}))
    body = submit(client, 'mixed.pdf', mixed_pdf(samples, tmp_path)).json()
    job = client.get(f"/api/jobs/{body['job']['id']}").json()
    assert job['status'] == 'succeeded' and job['warnings'] == [{'page': 2, 'step': 'ocr', 'error': 'ocr_failure'}]
    document = session.get(Document, uuid.UUID(body['document']['id']))
    session.refresh(document)
    assert document.status == DocumentStatus.ready and document.page_count == 2
    assert {chunk.page for chunk in document.chunks} == {1}


def test_ocr_page_boxes_are_scaled_to_pdf_points(api, samples, tmp_path):
    client, _, _, session, _ = api()
    body = submit(client, 'mixed.pdf', mixed_pdf(samples, tmp_path)).json()
    document = session.get(Document, uuid.UUID(body['document']['id']))
    session.refresh(document)
    ocr_chunk = next(chunk for chunk in document.chunks if chunk.page == 2)
    # FakeOCR words are 90 px wide at 200 dpi -> 32.4 pt; the line spans several words.
    assert ocr_chunk.bboxes[0]['page'] == 2 and ocr_chunk.bboxes[0]['x0'] == 0.0
    assert ocr_chunk.bboxes[0]['y0'] == pytest.approx(10 * 72 / 200, abs=0.1)


def test_list_paginates_and_searches(documents_api, samples):
    client, *_ = documents_api
    for name in ('sample.pdf', 'sample.docx', 'sample.xlsx'):
        assert submit(client, f'Báo cáo {name}', (samples / name).read_bytes()).status_code == 202
    page = client.get('/api/documents', params={'page': 1, 'page_size': 2}).json()
    assert page['total'] == 3 and len(page['items']) == 2 and page['page_size'] == 2
    assert page['items'][0]['filename'] == 'Báo cáo sample.xlsx'  # newest first
    assert page['items'][0]['latest_job']['status'] == 'succeeded'
    assert len(client.get('/api/documents', params={'page': 2, 'page_size': 2}).json()['items']) == 1
    found = client.get('/api/documents', params={'q': 'DOCX'}).json()
    assert [item['filename'] for item in found['items']] == ['Báo cáo sample.docx']
    assert client.get('/api/documents', params={'q': '100%_'}).json()['total'] == 0
    assert client.get('/api/documents', params={'page_size': 101}).status_code == 422


def test_get_and_stream_file(documents_api, samples):
    client, *_ = documents_api
    data = (samples / 'sample.pdf').read_bytes()
    document_id = submit(client, 'Quy chế.pdf', data).json()['document']['id']
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
    assert client.get(f'/api/jobs/{missing}').status_code == 404
    assert client.get(f'/api/jobs/{missing}/events').status_code == 404


def test_delete_removes_file_rows_and_vectors(documents_api, samples):
    client, store, storage, session = documents_api
    keep = submit(client, 'keep.docx', (samples / 'sample.docx').read_bytes()).json()['document']['id']
    gone = submit(client, 'gone.pdf', (samples / 'sample.pdf').read_bytes()).json()['document']['id']
    assert client.delete(f'/api/documents/{gone}').status_code == 204
    assert client.get(f'/api/documents/{gone}').status_code == 404
    assert client.delete(f'/api/documents/{gone}').status_code == 404
    remaining = store.collection.get(include=['metadatas'])['metadatas']
    assert {meta['document_id'] for meta in remaining} == {keep}
    assert len(storage.objects) == 1 and session.query(Document).count() == 1
    assert session.query(IngestJob).count() == 1


def test_search_after_upload(documents_api, samples):
    client, *_ = documents_api
    submit(client, 'sample.docx', (samples / 'sample.docx').read_bytes())
    hits = client.post('/questions/search', json={'question': 'nghỉ phép'}).json()['results']
    assert hits[0]['metadata']['filename'] == 'sample.docx' and hits[0]['id']


def test_invalid_query_and_filename_are_rejected(documents_api):
    client, *_ = documents_api
    assert client.get('/api/documents', params={'q': 'a\x00b'}).status_code == 422
    assert client.get('/api/documents', params={'page': 10**17}).status_code == 422
    # HTTP clients escape control characters in multipart names, so check the parser directly.
    import io
    from types import SimpleNamespace
    from app.api.documents import read_upload
    from app.core.errors import PipelineError
    with pytest.raises(PipelineError) as caught:
        read_upload(SimpleNamespace(filename='bad\x00name.pdf', file=io.BytesIO(b'%PDF')), Settings(_env_file=None))
    assert (caught.value.code, caught.value.status_code) == ('invalid_filename', 400)


def test_reindex_replaces_chunks_and_vectors(documents_api, samples):
    client, store, storage, session = documents_api
    document_id = submit(client, 'sample.docx', (samples / 'sample.docx').read_bytes()).json()['document']['id']
    document = session.get(Document, uuid.UUID(document_id))
    old_ids = {str(chunk.id) for chunk in document.chunks}
    job = client.post(f'/api/documents/{document_id}/reindex')
    assert job.status_code == 202
    session.expire_all()
    document = session.get(Document, uuid.UUID(document_id))
    new_ids = {str(chunk.id) for chunk in document.chunks}
    assert document.status == DocumentStatus.ready and new_ids and new_ids.isdisjoint(old_ids)
    assert set(store.collection.get()['ids']) == new_ids  # no leftover vectors from before
    storage.objects.clear()
    assert client.get(f"/api/jobs/{client.post(f'/api/documents/{document_id}/reindex').json()['id']}").json()[
        'error_code'] == 'file_not_found'
    session.expire_all()
    assert session.get(Document, uuid.UUID(document_id)).status == DocumentStatus.failed
    assert store.collection.count() == 0


def test_document_deleted_during_processing_leaves_no_vectors(api, samples, db_engine):
    from sqlalchemy import text
    client, store, _, session, service = api()
    ingestion = service.queue.runner.ingestion
    original = ingestion.process

    def process_then_delete(data, filename, document_id, **kwargs):
        processed = original(data, filename, document_id, **kwargs)
        with db_engine.begin() as connection:
            connection.execute(text('DELETE FROM documents WHERE id = :id'), {'id': document_id})
        return processed
    ingestion.process = process_then_delete
    response = submit(client, 'sample.docx', (samples / 'sample.docx').read_bytes())
    assert response.status_code == 409 and response.json()['error'] == 'document_deleted'
    assert store.collection.count() == 0 and session.query(Document).count() == 0
