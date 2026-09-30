"""Configuration and operations fixes: database URL from parts, summary-model health, packaging privacy."""
from types import SimpleNamespace

from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

from app.api.system import model_listed
from app.core.config import Settings
from app.core.dependencies import get_llm, get_summary_llm
from app.main import app
from scripts.package_submission import submission_files


def test_database_url_is_built_from_parts_with_any_password():
    settings = Settings(_env_file=None, database_url='', postgres_user='rag', postgres_password='p@ss:w/rd%#?',
                        postgres_host='postgres', postgres_port=5432, postgres_db='rag')
    url = make_url(settings.database_url)
    assert (url.host, url.port, url.database, url.username) == ('postgres', 5432, 'rag', 'rag')
    assert url.password == 'p@ss:w/rd%#?'


def test_explicit_database_url_wins():
    settings = Settings(_env_file=None, database_url='postgresql+psycopg://u:p@db.example:6543/x',
                        postgres_host='postgres')
    assert make_url(settings.database_url).host == 'db.example'


def test_model_listed_matches_latest_tag():
    assert model_listed('qwen2.5:3b', ['qwen2.5:7b', 'qwen2.5:3b'])
    assert model_listed('llama3', ['llama3:latest'])
    assert not model_listed('qwen2.5:3b', ['qwen2.5:7b'])


class FakeLLM:
    def __init__(self, model, models):
        self.model, self.models = model, models

    def health(self):
        return {'status': 'ok', 'model': self.model, 'model_available': True, 'models': self.models}


def test_health_llm_reports_a_missing_summary_model():
    main = FakeLLM('qwen2.5:7b', ['qwen2.5:7b'])
    app.dependency_overrides[get_llm] = lambda: main
    app.dependency_overrides[get_summary_llm] = lambda: SimpleNamespace(model='qwen2.5:3b')
    try:
        with TestClient(app) as client:
            body = client.get('/health/llm').json()
        assert body['status'] == 'degraded' and body['summary_model_available'] is False
        assert 'models' not in body and 'base_url' not in body
        main.models.append('qwen2.5:3b')
        with TestClient(app) as client:
            assert client.get('/health/llm').json()['status'] == 'ok'
        app.dependency_overrides[get_summary_llm] = lambda: None
        with TestClient(app) as client:
            assert 'summary_model' not in client.get('/health/llm').json()
    finally:
        app.dependency_overrides.pop(get_llm, None)
        app.dependency_overrides.pop(get_summary_llm, None)


def test_submission_never_packs_private_evaluation_data_or_env(tmp_path):
    for relative in ('app/main.py', 'migrations/env.py', 'docker/seaweedfs/start.sh', 'evaluation/questions.json',
                     'evaluation/custom/questions.json', 'evaluation/custom/private.pdf', 'evaluation/results_llm.json',
                     '.env', 'frontend/.env.local', 'README.md', 'compose.yaml', 'notes.txt', 'data/uploads/a.pdf'):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('x', encoding='utf-8')
    packed = {path.relative_to(tmp_path).as_posix() for path in submission_files(tmp_path)}
    assert {'app/main.py', 'migrations/env.py', 'docker/seaweedfs/start.sh', 'evaluation/questions.json',
            'README.md', 'compose.yaml'} <= packed
    assert not packed & {'evaluation/custom/questions.json', 'evaluation/custom/private.pdf',
                         'evaluation/results_llm.json', '.env', 'frontend/.env.local', 'notes.txt',
                         'data/uploads/a.pdf'}
