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
    """OCR double: OCR_TEXT as one line of words, 100 px wide each, at the top of the image."""
    parallel = False

    def __init__(self, fail_calls: set[int] | None = None):
        self.calls = 0
        self.fail_calls = fail_calls or set()

    def extract_words(self, image):
        from app.core.errors import PipelineError
        from app.services.layout import Word
        self.calls += 1
        assert image.width > 0
        if self.calls in self.fail_calls:
            raise PipelineError('ocr_failure', 'OCR failed.', 503)
        return [Word(text, 100 * i, 10, 100 * i + 90, 40, block=0, line=0) for i, text in enumerate(OCR_TEXT.split())]

    def extract(self, image):
        from app.services.layout import build_text
        return build_text(self.extract_words(image))[0]


class WordTokenizer:
    def num_special_tokens_to_add(self, pair=False):
        return 2

    def __call__(self, text, **kwargs):
        return {'offset_mapping': [match.span() for match in re.finditer(r'\S+', text)]}


class FakeEmbedding:
    model = SimpleNamespace(tokenizer=WordTokenizer(), max_seq_length=128)

    def embed_documents(self, texts, progress=None):
        if progress is not None:
            progress(1.0)
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


def _test_database_url() -> str:
    import os
    from app.core.config import Settings
    from scripts.isolation import scratch_database_url
    return os.environ.get('TEST_DATABASE_URL') or scratch_database_url(Settings().database_url, '_test')


@pytest.fixture(scope='session')
def db_engine():
    """The <db>_test database migrated with Alembic (also checks the migrations apply)."""
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, text
    url = _test_database_url()
    engine = create_engine(url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            connection.execute(text('select 1'))
    except Exception as exc:
        pytest.skip(f'PostgreSQL test database unavailable ({type(exc).__name__}); run tests via docker compose')
    config = Config('alembic.ini')
    config.set_main_option('sqlalchemy.url', url.replace('%', '%%'))
    command.upgrade(config, 'head')
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine):
    from sqlalchemy import text
    from sqlalchemy.orm import sessionmaker
    with db_engine.begin() as connection:
        connection.execute(text('TRUNCATE documents, conversations CASCADE'))
    with sessionmaker(bind=db_engine, expire_on_commit=False)() as session:
        yield session


@pytest.fixture
def memory_storage():
    from tests.fakes import InMemoryStorage
    return InMemoryStorage()
