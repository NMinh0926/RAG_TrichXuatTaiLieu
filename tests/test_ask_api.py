"""Legacy non-streaming POST /api/questions/ask (kept for backward compatibility) and /health/llm."""
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.api import questions
from app.core.dependencies import get_generation, get_llm
from app.llm.prompts import INSUFFICIENT_ANSWER, NO_CONTEXT_ANSWER
from tests.test_generation import BASE_URL, MODEL, hit, service_with_reply


@pytest.fixture
def ask_client(monkeypatch):
    retriever = Mock()
    retriever.search_candidates.return_value = [hit()]
    monkeypatch.setattr(questions, 'get_services', lambda: (None, retriever))
    try:
        with TestClient(app) as client:
            yield client, retriever
    finally:
        app.dependency_overrides.clear()


def test_ask_success(ask_client):
    client, _ = ask_client
    service, _ = service_with_reply('12 ngày [1]')
    app.dependency_overrides[get_generation] = lambda: service
    response = client.post('/api/questions/ask', json={'question': 'Nghỉ bao nhiêu ngày?'})
    assert response.status_code == 200
    assert response.json() == {'answer': '12 ngày [1]', 'sources': [{'filename': 'policy.pdf', 'page': 1}]}


def test_ask_no_candidates_skips_llm(ask_client):
    client, retriever = ask_client
    retriever.search_candidates.return_value = []
    service, fake = service_with_reply('never')
    app.dependency_overrides[get_generation] = lambda: service
    response = client.post('/api/questions/ask', json={'question': 'CEO của Microsoft là ai?'})
    assert response.json() == {'answer': NO_CONTEXT_ANSWER, 'sources': []}
    assert fake.requests == []


def test_ask_insufficient_context(ask_client):
    client, _ = ask_client
    service, fake = service_with_reply(INSUFFICIENT_ANSWER)
    app.dependency_overrides[get_generation] = lambda: service
    response = client.post('/api/questions/ask', json={'question': 'CEO của Microsoft là ai?'})
    assert response.status_code == 200
    assert response.json() == {'answer': INSUFFICIENT_ANSWER, 'sources': []}
    assert len(fake.chats) == 1


def test_ask_llm_not_running_is_503_and_recovers(ask_client, caplog):
    client, _ = ask_client
    service, fake = service_with_reply(error=httpx.ConnectError('connection refused'))
    app.dependency_overrides[get_generation] = lambda: service
    response = client.post('/api/questions/ask', json={'question': 'leave days'})
    assert response.status_code == 503
    assert response.json()['error'] == 'llm_unavailable' and BASE_URL in response.json()['detail']
    assert 'llm unreachable' in caplog.text
    fake.error, fake.reply = None, '12 days [1]'
    assert client.post('/api/questions/ask', json={'question': 'leave days'}).status_code == 200
    assert client.get('/health').status_code == 200


def test_ask_model_not_pulled_is_503(ask_client):
    client, _ = ask_client
    service, _ = service_with_reply(status=404)
    app.dependency_overrides[get_generation] = lambda: service
    response = client.post('/api/questions/ask', json={'question': 'leave days'})
    assert response.status_code == 503
    assert response.json()['detail'] == f'Model {MODEL} chưa được tải. Chạy: ollama pull {MODEL}'


def test_ask_retrieval_failure(ask_client):
    client, retriever = ask_client
    retriever.search_candidates.side_effect = RuntimeError('private internal details')
    response = client.post('/api/questions/ask', json={'question': 'leave days'})
    assert response.status_code == 503
    assert response.json()['error'] == 'retrieval_failure'
    assert 'private internal' not in response.text


@pytest.mark.parametrize('question', ['', '  ', '\n\t'])
def test_ask_empty_question(ask_client, question):
    client, retriever = ask_client
    assert client.post('/api/questions/ask', json={'question': question}).status_code == 422
    retriever.search_candidates.assert_not_called()


def test_llm_health_endpoint():
    service, fake = service_with_reply()
    app.dependency_overrides[get_llm] = lambda: service.client
    try:
        with TestClient(app) as client:
            response = client.get('/health/llm')
            assert response.status_code == 200
            assert response.json()['model'] == MODEL and response.json()['model_available'] is True
            fake.models = ('llama3:8b',)
            missing = client.get('/health/llm')
            assert missing.status_code == 503 and missing.json()['error'] == 'llm_model_missing'
            fake.error = httpx.ConnectError('refused')
            assert client.get('/health/llm').json()['error'] == 'llm_unavailable'
    finally:
        app.dependency_overrides.clear()
