import pytest
from app.core.config import Settings
from app.core.errors import PipelineError
from app.models.schemas import DocumentUnit, Chunk
from app.services.chunking import chunk_units
from app.services.ingestion import IngestionService
from app.services.retrieval import RetrievalService
from app.services.text_cleaner import clean_text
from app.services.vector_store import VectorStore
from tests.conftest import WordTokenizer, FakeEmbedding


def test_06_chunk_metadata_overlap_and_coverage():
    text = ' '.join(f'token{i}' for i in range(260))
    unit = DocumentUnit(content=text, metadata={'filename': 'p.pdf', 'page': 5})
    chunks = chunk_units([unit], WordTokenizer(), 700, 120, 128)
    assert len(chunks) > 1
    assert all(chunk.metadata['page'] == 5 for chunk in chunks)
    assert all(chunk.chunk_index == chunk.metadata['chunk_index'] for chunk in chunks)
    assert all(len(chunk.content.split()) <= 126 for chunk in chunks)
    assert set(text.split()) == {word for chunk in chunks for word in chunk.content.split()}
    assert set(chunks[0].content.split()) & set(chunks[1].content.split())
    assert 'chunk_index' not in unit.metadata


def test_cleaning_preserves_values():
    assert clean_text('  Giá:  12.5%\r\n\n\nMã: A-01 + 2 = 3  ') == 'Giá: 12.5%\n\nMã: A-01 + 2 = 3'


def test_invalid_config():
    with pytest.raises(ValueError):
        Settings(chunk_size=10, chunk_overlap=10, _env_file=None)


def test_08_chroma_persists(tmp_path):
    store = VectorStore(tmp_path, 'test-model', 700, 120)
    chunk = Chunk(content='annual leave 12 days', chunk_index=0,
                  metadata={'filename': 'p.pdf', 'page': 1, 'sheet': None, 'chunk_index': 0})
    store.add('abc', [chunk], [[1.0, 0.0, 0.0]])
    reopened = VectorStore(tmp_path, 'test-model', 700, 120)
    hit = reopened.search([1.0, 0.0, 0.0], 4)[0]
    assert hit.content == chunk.content
    assert hit.metadata['page'] == 1
    assert hit.relevance == pytest.approx(1)
    store.add('abc', [chunk], [[1.0, 0.0, 0.0]])
    assert reopened.collection.count() == 1


def test_threshold_and_empty_store(tmp_path):
    settings = Settings(retrieval_threshold=0.9, _env_file=None)
    store = VectorStore(tmp_path, 'test', 700, 120)
    retrieval = RetrievalService(settings, FakeEmbedding(), store)
    assert retrieval.retrieve('question') == []
    store.add('abc', [Chunk(content='unrelated', chunk_index=0, metadata={'filename': 'p.pdf'})],
              [[0.0, 1.0, 0.0]])
    assert retrieval.retrieve('question') == []


def test_ingestion_and_duplicate(samples, tmp_path, fake_ocr):
    settings = Settings(_env_file=None)
    store = VectorStore(tmp_path, 'test', 700, 120)
    service = IngestionService(settings, fake_ocr, FakeEmbedding(), store)
    result = service.ingest(samples / 'sample.docx')
    repeated = service.ingest(samples / 'sample.docx')
    assert result.document_id == repeated.document_id
    assert store.collection.count() == result.chunks
    assert store.search([1.0, 0.0, 0.0], 1)[0].metadata['filename'] == 'sample.docx'


@pytest.mark.parametrize('name,content,code', [
    ('empty.pdf', b'', 'empty_document'), ('bad.pdf', b'corrupt PDF', 'extraction_failure'),
])
def test_bad_document(tmp_path, fake_ocr, name, content, code):
    path = tmp_path / name
    path.write_bytes(content)
    service = IngestionService(Settings(_env_file=None), fake_ocr, FakeEmbedding(), None)
    with pytest.raises(PipelineError) as error:
        service.ingest(path)
    assert error.value.code == code


def test_blank_docx(tmp_path, fake_ocr):
    from docx import Document
    path = tmp_path / 'blank.docx'
    Document().save(path)
    with pytest.raises(PipelineError) as error:
        IngestionService(Settings(_env_file=None), fake_ocr, FakeEmbedding(), None).ingest(path)
    assert error.value.code == 'no_usable_text'


def test_ocr_failure_is_typed(samples):
    from app.services.ocr_service import OCRService
    from app.services.image_loader import load_image
    class BrokenEngine:
        def predict(self, image):
            raise RuntimeError('inference failed')
    ocr = OCRService()
    ocr._engine = BrokenEngine()
    with pytest.raises(PipelineError) as error:
        load_image((samples / 'sample.jpg').read_bytes(), 'sample.jpg', ocr)
    assert error.value.code == 'ocr_failure'


def test_embedding_failure_is_typed():
    from app.services.embedding import EmbeddingService
    class BrokenModel:
        def encode(self, texts, **kwargs):
            raise RuntimeError('inference failed')
    embedding = EmbeddingService('test')
    embedding._model = BrokenModel()
    with pytest.raises(PipelineError) as error:
        embedding.embed_query('question')
    assert error.value.code == 'embedding_failure'


def test_vector_failure_is_typed(tmp_path, monkeypatch):
    store = VectorStore(tmp_path, 'test', 700, 120)
    def fail(**kwargs):
        raise RuntimeError('disk full')
    monkeypatch.setattr(type(store.collection), 'upsert', lambda self, **kwargs: fail(**kwargs))
    with pytest.raises(PipelineError) as error:
        store.add('abc', [Chunk(content='text', chunk_index=0, metadata={'filename': 'a.pdf'})], [[1., 0.]])
    assert error.value.code == 'vector_store_failure'
