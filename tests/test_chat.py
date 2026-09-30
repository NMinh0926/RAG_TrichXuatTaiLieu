"""POST /api/chat (SSE), conversations API, intent rules, map-reduce summaries. LLM HTTP is mocked."""
import json
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.core.dependencies import get_chat_service
from app.db.models import Chunk, Document, DocumentStatus, DocumentSummary, Message, MessageRole
from app.db.session import get_db
from app.llm.client import LLMClient
from app.llm.prompts import INSUFFICIENT_ANSWER, NO_CONTEXT_ANSWER
from app.main import app
from app.models.schemas import SearchHit
from app.services.chat import ChatService
from app.services.generation import GenerationService
from app.services.intent import classify
from app.services.retrieval import RetrievalService
from app.services.summarizer import document_text, group_by_budget
from tests.conftest import FakeEmbedding
from tests.test_generation import BASE_URL, MODEL, FakeLLM, user_message


class RecordingStore:
    """Vector store double: fixed hits, records the queries' document filters."""

    def __init__(self, hits):
        self.hits, self.filters = hits, []

    def search(self, embedding, top_k, document_ids=None):
        self.filters.append(document_ids)
        return self.hits[:top_k]


def chunk_hit(text, document_id='doc-1', page=1, chunk_id='chunk-1'):
    return SearchHit(id=chunk_id, content=text, metadata={'filename': 'quy_che.pdf', 'page': page,
                                                          'document_id': document_id}, relevance=0.4, distance=0.6)


@pytest.fixture
def chat(db_engine, db_session, db_user):
    # Retrieval only searches documents the user may read: one shared ready document (the store double
    # returns its fixed hits whatever the filter).
    db_session.add(Document(filename='library.pdf', content_type='application/pdf', size_bytes=1, sha256='a' * 64,
                            object_key='documents/library.pdf', status=DocumentStatus.ready, is_shared=True))
    db_session.commit()

    def make(reply, hits=(), **overrides):
        settings = Settings(_env_file=None, llm_base_url=BASE_URL, llm_model=MODEL, top_k=4, **overrides)
        fake = FakeLLM(reply)
        llm = LLMClient(settings, transport=httpx.MockTransport(fake))
        store = RecordingStore(list(hits))
        service = ChatService(sessionmaker(bind=db_engine, expire_on_commit=False),
                              RetrievalService(settings, FakeEmbedding(), store), GenerationService(settings, llm),
                              llm, settings)
        app.dependency_overrides[get_chat_service] = lambda: service
        app.dependency_overrides[get_db] = lambda: db_session
        return TestClient(app), fake, store
    yield make
    app.dependency_overrides.clear()


def stream(client, **body) -> list[tuple[str, dict]]:
    events, kind = [], None
    with client.stream('POST', '/api/chat', json=body) as response:
        assert response.status_code == 200, response.read()
        assert response.headers['content-type'].startswith('text/event-stream')
        for line in response.iter_lines():
            if line.startswith('event: '):
                kind = line[7:]
            elif line.startswith('data: '):
                events.append((kind, json.loads(line[6:])))
    return events


def of(events, kind):
    return [data for event, data in events if event == kind]


def test_rag_turn_streams_tokens_and_stores_cited_sources(chat, db_session):
    hits = [chunk_hit('Thử việc 45 ngày.', chunk_id='c1'), chunk_hit('Nghỉ phép 16 ngày.', page=2, chunk_id='c2')]
    client, fake, _ = chat('Nhân viên được **nghỉ phép 16 ngày** mỗi năm [2].', hits)
    events = stream(client, question='Nghỉ phép bao nhiêu ngày?')
    assert [e for e, _ in events][:2] == ['meta', 'meta'] and events[-1][0] == 'done'
    conversation_id = of(events, 'meta')[0]['conversation_id']
    assert of(events, 'meta')[1] == {'intent': 'qa', 'question': 'Nghỉ phép bao nhiêu ngày?'}
    assert ''.join(t['text'] for t in of(events, 'token')) == 'Nhân viên được **nghỉ phép 16 ngày** mỗi năm [2].'
    done = of(events, 'done')[0]
    assert done['sources'] == [{'n': 2, 'chunk_id': 'c2', 'document_id': 'doc-1', 'filename': 'quy_che.pdf',
                                'page': 2, 'sheet': None, 'excerpt': 'Nghỉ phép 16 ngày.', 'bboxes': None}]
    assert len(fake.chats) == 1 and fake.chats[0]['stream'] is True  # no rewrite on the first turn
    messages = db_session.query(Message).filter_by(conversation_id=uuid.UUID(conversation_id)).order_by(Message.seq).all()
    assert [(m.role, m.content) for m in messages] == [(MessageRole.user, 'Nghỉ phép bao nhiêu ngày?'),
                                                       (MessageRole.assistant, done['answer'])]
    assert messages[1].sources == done['sources'] and str(messages[1].id) == done['message_id']
    detail = client.get(f'/api/conversations/{conversation_id}').json()
    assert detail['title'] == 'Nghỉ phép bao nhiêu ngày?' and len(detail['messages']) == 2
    assert detail['messages'][1]['sources'][0]['chunk_id'] == 'c2'


def test_follow_up_is_rewritten_with_history(chat):
    def reply(body):
        if body['messages'][0]['content'].startswith('Viết lại'):
            assert 'Nghỉ phép bao nhiêu ngày?' in user_message(body)
            return 'Nhân viên thử việc có được nghỉ phép không?'
        return 'Không [1].'
    client, fake, _ = chat(reply, [chunk_hit('Thử việc không được nghỉ phép.')])
    first = stream(client, question='Nghỉ phép bao nhiêu ngày?')
    conversation_id = of(first, 'meta')[0]['conversation_id']
    second = stream(client, question='Còn thử việc thì sao?', conversation_id=conversation_id)
    assert of(second, 'meta')[1]['question'] == 'Nhân viên thử việc có được nghỉ phép không?'
    assert 'CÂU HỎI: Nhân viên thử việc có được nghỉ phép không?' in user_message(fake.chats[-1])
    assert client.get(f'/api/conversations/{conversation_id}').json()['messages'][2]['content'] == 'Còn thử việc thì sao?'


def test_scope_filters_retrieval_and_unknown_ids_are_404(chat, db_session):
    document = Document(filename='a.pdf', content_type='application/pdf', size_bytes=1, sha256='f' * 64,
                        object_key='documents/a.pdf', status=DocumentStatus.ready, is_shared=True)
    db_session.add(document)
    db_session.commit()
    client, _, store = chat('Có [1].', [chunk_hit('x', document_id=str(document.id))])
    stream(client, question='q', document_ids=[str(document.id)])
    assert store.filters[-1] == [str(document.id)]
    assert client.post('/api/chat', json={'question': 'q', 'document_ids': [str(uuid.uuid4())]}).status_code == 404
    assert client.post('/api/chat', json={'question': 'q', 'conversation_id': str(uuid.uuid4())}).json()['error'] \
        == 'conversation_not_found'


def test_refusal_is_replaced_and_has_no_sources(chat):
    client, _, _ = chat(f'{INSUFFICIENT_ANSWER} [1]', [chunk_hit('Không liên quan.')])
    events = stream(client, question='CEO là ai?')
    assert of(events, 'replace') == [{'text': INSUFFICIENT_ANSWER}]
    assert of(events, 'done')[0]['answer'] == INSUFFICIENT_ANSWER and of(events, 'done')[0]['sources'] == []


def test_no_candidates_answer_without_llm(chat):
    client, fake, _ = chat('never', [])
    events = stream(client, question='q')
    assert of(events, 'done')[0]['answer'] == NO_CONTEXT_ANSWER and fake.chats == []


def test_llm_failure_is_an_error_event_and_answer_is_not_stored(chat, db_session):
    client, fake, _ = chat('x', [chunk_hit('y')])
    fake.error = httpx.ConnectError('refused')
    events = stream(client, question='q')
    error = of(events, 'error')[0]
    assert error['error'] == 'llm_unavailable' and error['status'] == 503 and not of(events, 'done')
    assert [m.role for m in db_session.query(Message)] == [MessageRole.user]


def _document_with_chunks(session, texts: list[str]) -> Document:
    document = Document(filename='quy_che.pdf', content_type='application/pdf', size_bytes=1, sha256='e' * 64,
                        object_key='documents/q.pdf', status=DocumentStatus.ready, chunk_count=len(texts),
                        is_shared=True)
    session.add(document)
    session.flush()
    position = 0
    for ordinal, text in enumerate(texts):
        session.add(Chunk(document_id=document.id, ordinal=ordinal, page=ordinal // 2 + 1, content=text,
                          char_start=position if ordinal % 2 else 0, char_end=(position if ordinal % 2 else 0) + len(text)))
        position = len(text) + 1
    session.commit()
    return document


def test_summary_map_reduce_streams_progress_and_is_cached(chat, db_session):
    document = _document_with_chunks(db_session, [f'Chương {i}. ' + 'Quy định chi tiết. ' * 90 for i in range(6)])

    def reply(body):
        system = body['messages'][0]['content']
        if system.startswith('Bạn tóm tắt MỘT PHẦN'):
            return '- ý chính của phần'
        return '## Tổng quan\nQuy chế nội bộ.'
    client, fake, _ = chat(reply, [chunk_hit('x', document_id=str(document.id))], llm_context_tokens=2048)
    events = stream(client, question='Tóm tắt tài liệu quy_che.pdf')
    assert of(events, 'meta')[1]['intent'] == 'summary'
    stages = [p['stage'] for p in of(events, 'progress')]
    assert stages.count('map') >= 2 and stages[-1] == 'reduce'
    done = of(events, 'done')[0]
    assert done['answer'] == '## Tổng quan\nQuy chế nội bộ.'
    assert done['sources'] == [{'n': 1, 'chunk_id': None, 'document_id': str(document.id), 'filename': 'quy_che.pdf',
                                'page': None, 'sheet': None, 'excerpt': None, 'bboxes': None}]
    calls = len(fake.chats)
    assert db_session.get(DocumentSummary, document.id).content == done['answer']
    again = stream(client, question='Tóm tắt lại quy_che.pdf giúp tôi')
    assert of(again, 'progress') == [{'stage': 'cached', 'document': 'quy_che.pdf'}]
    assert of(again, 'done')[0]['answer'] == done['answer'] and len(fake.chats) == calls


def test_document_text_drops_overlap_and_groups_fit_budget():
    chunks = [Chunk(ordinal=0, page=1, content='một hai ba bốn', char_start=0, char_end=14),
              Chunk(ordinal=1, page=1, content='ba bốn năm sáu', char_start=8, char_end=22),
              Chunk(ordinal=2, page=2, content='trang hai', char_start=0, char_end=9)]
    assert document_text(chunks) == ['[Trang 1]\nmột hai ba bốn năm sáu', '[Trang 2]\ntrang hai']
    groups = group_by_budget(['a' * 30, 'b' * 30, 'c' * 100], 70)
    assert all(len(group) <= 70 for group in groups) and ''.join(groups).replace('\n', '').count('c') == 100


@pytest.mark.parametrize('question,intent', [
    ('Tóm tắt tài liệu này', 'summary'), ('Liệt kê các ý chính', 'summary'), ('Give me an overview', 'summary'),
    ('Nội dung chính của quy chế là gì?', 'summary'), ('Phụ cấp ăn trưa bao nhiêu?', 'qa'),
])
def test_intent_rules(question, intent):
    assert classify(question, llm=None).intent == intent


def test_ambiguous_intent_asks_llm_and_survives_llm_failure():
    settings = Settings(_env_file=None, llm_base_url=BASE_URL, llm_model=MODEL)
    fake = FakeLLM('summary')
    llm = LLMClient(settings, transport=httpx.MockTransport(fake))
    result = classify('Tài liệu này nói về gì?', llm)
    assert (result.intent, result.source) == ('summary', 'llm') and fake.chats[0]['max_tokens'] == 5
    fake.error = httpx.ConnectError('refused')
    assert classify('Tài liệu này nói về gì?', llm).intent == 'qa'


def test_conversations_list_and_delete(chat):
    client, _, _ = chat('A [1].', [chunk_hit('x')])
    ids = [of(stream(client, question=f'câu {i}'), 'meta')[0]['conversation_id'] for i in range(3)]
    listing = client.get('/api/conversations', params={'page_size': 2}).json()
    assert listing['total'] == 3 and [c['id'] for c in listing['items']] == ids[::-1][:2]
    assert client.delete(f'/api/conversations/{ids[0]}').status_code == 204
    assert client.get(f'/api/conversations/{ids[0]}').status_code == 404
    assert client.get('/api/conversations').json()['total'] == 2


def test_summary_map_model_is_used_for_sections_only(db_session):
    from app.services.summarizer import SummaryService
    document = _document_with_chunks(db_session, [f'Chương {i}. ' + 'Quy định chi tiết. ' * 90 for i in range(6)])
    settings = Settings(_env_file=None, llm_base_url=BASE_URL, llm_model=MODEL)
    main, small = FakeLLM('## Tổng quan\nKết quả.'), FakeLLM('- ý chính')
    service = SummaryService(db_session, LLMClient(settings, transport=httpx.MockTransport(main)), 2048,
                             LLMClient(settings, transport=httpx.MockTransport(small), model='qwen2.5:3b'))
    events = list(service.summarize(document, document.chunks))
    assert ''.join(e.data for e in events if e.kind == 'token') == '## Tổng quan\nKết quả.'
    assert {chat['model'] for chat in small.chats} == {'qwen2.5:3b'} and len(small.chats) >= 2
    assert [chat['model'] for chat in main.chats] == [MODEL]  # only the final summary
    assert db_session.get(DocumentSummary, document.id).model == f'{MODEL}+qwen2.5:3b'
