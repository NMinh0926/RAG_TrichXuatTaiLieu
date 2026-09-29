"""Real model tests. No mocks, no skip-on-download-failure, no fake semantic vectors."""
import pytest
import numpy as np
from app.core.config import Settings
from app.services.pdf_loader import load_pdf
from app.services.image_loader import load_image
from app.services.vector_store import VectorStore
from app.services.ingestion import IngestionService
from app.services.retrieval import RetrievalService

pytestmark = pytest.mark.integration


def test_02_real_scanned_pdf(samples, real_ocr):
    unit = load_pdf((samples / 'sample_scan.pdf').read_bytes(), 'sample_scan.pdf', real_ocr)[0]
    assert '12' in unit.content
    assert 'leave' in unit.content.lower()
    assert unit.metadata['page'] == 1
    assert unit.metadata['source_type'] == 'ocr'


def test_05_real_image_ocr(samples, real_ocr):
    for name in ['sample.jpg', 'sample.png']:
        unit = load_image((samples / name).read_bytes(), name, real_ocr)[0]
        assert '12' in unit.content and 'leave' in unit.content.lower()


def test_07_real_embedding_dimensions(real_embedding):
    docs = real_embedding.embed_documents(['Nghỉ phép 12 ngày.', 'Doanh thu tháng một.'])
    query = real_embedding.embed_query('Nhân viên được nghỉ mấy ngày?')
    assert len(docs[0]) == len(docs[1]) == len(query) == 384
    assert np.isfinite(docs).all()
    assert np.linalg.norm(query) == pytest.approx(1.0, abs=1e-5)


@pytest.fixture
def indexed_policy(samples, tmp_path, real_embedding):
    settings = Settings(_env_file=None)
    store = VectorStore(tmp_path, settings.embedding_model, settings.chunk_size, settings.chunk_overlap)
    IngestionService(settings, None, real_embedding, store).ingest(samples / 'sample.docx')
    return RetrievalService(settings, real_embedding, store)


def test_09_real_semantic_retrieval(indexed_policy):
    hits = indexed_policy.retrieve('Mỗi năm nhân viên được nghỉ bao nhiêu ngày?')
    assert hits
    assert '12' in hits[0].content
    assert hits[0].metadata['filename'] == 'sample.docx'


def test_10_real_out_of_context(indexed_policy):
    assert indexed_policy.retrieve('Khoảng cách từ Trái Đất đến sao Hỏa là bao nhiêu?') == []
