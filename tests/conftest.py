import re
from types import SimpleNamespace
import pytest
from scripts.create_samples import create_samples, OCR_TEXT


@pytest.fixture(scope='session')
def samples(tmp_path_factory):
    directory = tmp_path_factory.mktemp('samples')
    create_samples(directory)
    return directory


class FakeOCR:
    def __init__(self):
        self.calls = 0

    def extract(self, image):
        self.calls += 1
        assert image.width > 0
        return OCR_TEXT


class WordTokenizer:
    def num_special_tokens_to_add(self, pair=False):
        return 2

    def __call__(self, text, **kwargs):
        return {'offset_mapping': [match.span() for match in re.finditer(r'\S+', text)]}


class FakeEmbedding:
    model = SimpleNamespace(tokenizer=WordTokenizer(), max_seq_length=128)

    def embed_documents(self, texts):
        return [[1.0, 0.0, 0.0] for _ in texts]

    def embed_query(self, question):
        return [1.0, 0.0, 0.0]


@pytest.fixture
def fake_ocr():
    return FakeOCR()


@pytest.fixture(scope='session')
def real_embedding():
    from app.core.config import Settings
    from app.services.embedding import EmbeddingService
    return EmbeddingService(Settings(_env_file=None).embedding_model)


@pytest.fixture(scope='session')
def real_ocr():
    from app.services.ocr_service import OCRService
    return OCRService('vi')
