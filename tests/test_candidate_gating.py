"""/api/questions/ask: raw Top-K candidates -> LLM support decision -> answer / no-answer / 5xx.

Persistence across a process restart is covered by tests/test_e2e.py.
"""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api import questions
from app.core.config import Settings
from app.core.dependencies import get_generation
from app.main import app
from app.models.schemas import SearchHit
from app.llm.prompts import INSUFFICIENT_ANSWER, NO_CONTEXT_ANSWER
from app.services.retrieval import RetrievalService
from tests.conftest import FakeEmbedding
from tests.test_generation import service_with_reply, user_message

ORION = 'Project Orion code is ZXQ-91827.'
# config.py loads .env into os.environ, so _env_file=None alone does not isolate tests.
PINNED = {'retrieval_threshold': 0.55, 'candidate_min_relevance': -1.0, 'top_k': 4}


def pinned_settings(**overrides):
    return Settings(_env_file=None, **{**PINNED, **overrides})


def candidate(text, relevance, filename='orion.pdf', page=1):
    return SearchHit(content=text, metadata={'filename': filename, 'page': page},
                     relevance=relevance, distance=1 - relevance)


class FixedStore:
    """Vector store double returning a fixed ranked Top-K."""

    def __init__(self, hits):
        self.hits = hits

    def search(self, embedding, top_k, document_ids=None):
        return self.hits[:top_k]


@pytest.fixture
def ask(monkeypatch):
    def make(hits, generation, **settings):
        retrieval = RetrievalService(pinned_settings(**settings), FakeEmbedding(), FixedStore(hits))
        monkeypatch.setattr(questions, 'get_services', lambda: (None, retrieval))
        app.dependency_overrides[get_generation] = lambda: generation
        return TestClient(app), retrieval
    yield make
    app.dependency_overrides.clear()


def test_a_no_chunk_above_threshold_still_reaches_llm(ask):
    """No candidate passes RETRIEVAL_THRESHOLD, yet the relevant one is verified by the LLM."""
    hits = [candidate(ORION, 0.31), candidate('Unrelated travel policy.', 0.22, 'travel.pdf')]
    generation, fake = service_with_reply('Mã là ZXQ-91827 [1]')
    client, retrieval = ask(hits, generation)
    assert retrieval.retrieve('Project Orion code?') == []  # the old gate would have dropped everything
    response = client.post('/api/questions/ask', json={'question': 'What is the Project Orion code?'})
    assert response.status_code == 200
    assert response.json() == {'answer': 'Mã là ZXQ-91827 [1]',
                               'sources': [{'filename': 'orion.pdf', 'page': 1}]}
    message = user_message(fake.chats[0])
    assert ORION in message and 'Unrelated travel policy.' in message


def test_b_supported_answer_cites_only_supporting_context(ask):
    hits = [candidate('Unrelated travel policy.', 0.40, 'travel.pdf'), candidate(ORION, 0.35)]
    generation, _ = service_with_reply('ZXQ-91827 [2]')
    client, _ = ask(hits, generation)
    response = client.post('/api/questions/ask', json={'question': 'Project Orion code?'})
    assert response.json()['sources'] == [{'filename': 'orion.pdf', 'page': 1}]


def test_c_unsupported_candidates_return_insufficient(ask):
    hits = [candidate(ORION, 0.45), candidate('Unrelated travel policy.', 0.3, 'travel.pdf')]
    generation, fake = service_with_reply(INSUFFICIENT_ANSWER)
    client, _ = ask(hits, generation)
    response = client.post('/api/questions/ask', json={'question': "What is Project Orion's CEO?"})
    assert response.status_code == 200
    assert response.json() == {'answer': INSUFFICIENT_ANSWER, 'sources': []}
    assert len(fake.chats) == 1  # decided by the LLM, not by a similarity score


def test_no_candidates_return_no_answer_without_llm(ask):
    generation, fake = service_with_reply('never')
    client, _ = ask([], generation)
    assert client.post('/api/questions/ask', json={'question': 'q'}).json() == {'answer': NO_CONTEXT_ANSWER,
                                                                                'sources': []}
    assert fake.requests == []


@pytest.mark.parametrize('kwargs,code,status', [
    ({'error': httpx.ConnectError('refused')}, 'llm_unavailable', 503),
    ({'status': 404}, 'llm_model_missing', 503),
    ({'error': httpx.ReadTimeout('slow')}, 'llm_timeout', 504),
])
def test_d_llm_failure_is_service_error_not_no_answer(ask, kwargs, code, status):
    generation, _ = service_with_reply(**kwargs)
    client, _ = ask([candidate(ORION, 0.31)], generation)
    response = client.post('/api/questions/ask', json={'question': 'Project Orion code?'})
    assert response.status_code == status
    assert response.json()['error'] == code
    assert NO_CONTEXT_ANSWER not in response.text and INSUFFICIENT_ANSWER not in response.text


def test_candidate_floor_is_optional_and_configurable():
    hits = [candidate(ORION, 0.31), candidate('noise', 0.05, 'noise.pdf')]
    default = RetrievalService(pinned_settings(), FakeEmbedding(), FixedStore(hits))
    assert default.search_candidates('q') == hits
    floored = RetrievalService(pinned_settings(candidate_min_relevance=0.1), FakeEmbedding(), FixedStore(hits))
    assert floored.search_candidates('q') == hits[:1]


@pytest.mark.integration
def test_e_raw_ranking_keeps_relevant_chunk_in_top_k(tmp_path, real_embedding):
    """Real embeddings/Chroma: relevant chunk is in Top-K regardless of its absolute score."""
    from docx import Document
    from app.services.ingestion import IngestionService
    from app.services.vector_store import VectorStore
    settings = pinned_settings()
    store = VectorStore(tmp_path / 'chroma', settings.embedding_model, settings.chunk_size, settings.chunk_overlap)
    ingestion = IngestionService(settings, None, real_embedding, store)
    facts = {'orion.docx': 'Bài toán Alpha yêu cầu hệ thống xử lý tài liệu PDF. Mã kiểm thử của bài toán Alpha là ZXQ-91827.',
             'travel.docx': 'Hotel allowance is 850 credits per night. Meals are reimbursed up to 95 credits per day.',
             'hr.docx': 'Nhân viên chính thức được hưởng 12 ngày nghỉ phép mỗi năm. Thử việc kéo dài 60 ngày.',
             'ops.docx': 'Warehouse opens at 07:00 and closes at 19:00. Safety helmets must be yellow.'}
    for name, text in facts.items():
        document = Document()
        document.add_paragraph(text)
        document.save(tmp_path / name)
        ingestion.ingest(tmp_path / name)
    retrieval = RetrievalService(settings, real_embedding, store)
    hits = retrieval.search_candidates('Mã kiểm thử của bài toán Alpha là gì?')
    assert len(hits) == settings.top_k
    assert [hit.relevance for hit in hits] == sorted((hit.relevance for hit in hits), reverse=True)
    assert any('ZXQ-91827' in hit.content for hit in hits[:3])

