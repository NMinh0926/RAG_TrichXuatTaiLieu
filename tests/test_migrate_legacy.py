"""Legacy ./data/uploads + persistent Chroma → storage + PostgreSQL + new vector store."""
import shutil

from sqlalchemy.orm import sessionmaker

from app.core import dependencies
from app.core.config import Settings
from app.db import session as db_session_module
from app.db.models import Document, DocumentStatus
from app.services.ingestion import IngestionService
from app.services.vector_store import VectorStore
from scripts import migrate_legacy
from tests.conftest import FakeEmbedding, FakeOCR


def test_links_known_files_reindexes_unknown_and_is_rerunnable(samples, tmp_path, db_session, db_engine,
                                                               memory_storage, monkeypatch):
    settings = Settings(_env_file=None)
    legacy_chroma, uploads = tmp_path / 'legacy_chroma', tmp_path / 'uploads'
    uploads.mkdir()
    # Legacy layout: files under UUID names; only sample.docx was ever indexed.
    shutil.copy(samples / 'sample.docx', uploads / 'aaaa.docx')
    shutil.copy(samples / 'sample.pdf', uploads / 'bbbb.pdf')
    legacy_store = VectorStore(legacy_chroma, 'test-model', settings.chunk_size, settings.chunk_overlap)
    IngestionService(settings, FakeOCR(), FakeEmbedding(), legacy_store).ingest(samples / 'sample.docx')
    legacy_vectors = legacy_store.collection.count()

    new_store = VectorStore(tmp_path / 'new_chroma', 'test-model', settings.chunk_size, settings.chunk_overlap)
    ingestion = IngestionService(settings, FakeOCR(), FakeEmbedding(), new_store)
    monkeypatch.setattr(dependencies, 'get_services', lambda: (ingestion, None))
    monkeypatch.setattr(dependencies, 'get_storage', lambda: memory_storage)
    monkeypatch.setattr(db_session_module, 'get_sessionmaker',
                        lambda: sessionmaker(bind=db_engine, expire_on_commit=False))

    report = migrate_legacy.migrate(uploads, legacy_chroma)
    assert report['linked'] == ['sample.docx']  # original name recovered from the legacy id
    assert report['reindexed'] == ['bbbb.pdf']
    assert report['failed'] == [] and report['legacy_documents_without_file'] == []
    documents = {d.filename: d for d in db_session.query(Document)}
    linked = documents['sample.docx']
    assert linked.status == DocumentStatus.ready and linked.chunk_count == legacy_vectors
    assert memory_storage.read(linked.object_key) == (samples / 'sample.docx').read_bytes()
    vectors = new_store.collection.get(where={'document_id': str(linked.id)}, include=['metadatas'])
    assert set(vectors['ids']) == {str(chunk.id) for chunk in linked.chunks}
    assert new_store.collection.count() == legacy_vectors + documents['bbbb.pdf'].chunk_count

    rerun = migrate_legacy.migrate(uploads, legacy_chroma)
    assert rerun['skipped_existing'] == ['sample.docx', 'bbbb.pdf']
    assert rerun['linked'] == rerun['reindexed'] == []
