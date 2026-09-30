"""Chat pipeline fixes: follow-up rewrite fallbacks, pooled connections during generation, section-scoped and
multi-document summaries, summary budgets and cache writes, filename matching. LLM HTTP is mocked."""
import re
import unicodedata
import uuid

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.db.models import Chunk, Document, DocumentStatus, DocumentSummary, Message, MessageRole
from app.llm.client import LLMClient
from app.llm.prompts import REWRITE_MESSAGE_CHARS, rewrite_messages
from app.services.chat import (MULTI_INTRO_TOKENS, ChatService, demote_headings, mentioned_name, names_section,
                               valid_rewrite)
from app.services.generation import GenerationService
from app.services.retrieval import RetrievalService
from app.services.summarizer import CHARS_PER_TOKEN, SummaryService
from tests.conftest import FakeEmbedding
from tests.test_chat import RecordingStore, chunk_hit
from tests.test_generation import BASE_URL, MODEL, FakeLLM, user_message

REWRITE = 'Viết lại'
FILENAME = re.compile(r'tài liệu "([^"]+)"')


def is_rewrite(body: dict) -> bool:
    return body['messages'][0]['content'].startswith(REWRITE)


def add_document(session, filename: str, texts: list[str] = (), shared: bool = True) -> Document:
    document = Document(filename=filename, content_type='application/pdf', size_bytes=1, sha256=uuid.uuid4().hex * 2,
                        object_key=f'documents/{uuid.uuid4()}', status=DocumentStatus.ready, chunk_count=len(texts),
                        is_shared=shared)
    session.add(document)
    session.flush()
    for ordinal, content in enumerate(texts):
        session.add(Chunk(document_id=document.id, ordinal=ordinal, page=ordinal + 1, content=content,
                          char_start=0, char_end=len(content)))
    session.commit()
    return document


@pytest.fixture
def service(db_engine, db_session, db_user):
    def make(reply, hits=(), **overrides):
        settings = Settings(_env_file=None, llm_base_url=BASE_URL, llm_model=MODEL, top_k=4, **overrides)
        fake = FakeLLM(reply)
        llm = LLMClient(settings, transport=httpx.MockTransport(fake))
        store = RecordingStore(list(hits))
        chat = ChatService(sessionmaker(bind=db_engine, expire_on_commit=False),
                           RetrievalService(settings, FakeEmbedding(), store), GenerationService(settings, llm),
                           llm, settings)
        return chat, fake, store
    return make


def run(chat: ChatService, question: str, user, **kwargs) -> dict[str, list[dict]]:
    events: dict[str, list[dict]] = {}
    for event in chat.ask(question, user=user, **kwargs):
        events.setdefault(event.kind, []).append(event.data)
    return events


def answer_of(events: dict) -> str:
    return events['done'][0]['answer']


# --- B/C: follow-up rewrite -------------------------------------------------------------------------------------

def test_rewrite_prompt_clips_history():
    history = [('user', f'câu {i}') for i in range(4)] + [('assistant', '## Tổng quan\n' + 'Nội dung dài. ' * 300)]
    prompt = user_message({'messages': rewrite_messages(history, 'Còn gì nữa?')})
    assert 'câu 0' not in prompt and 'câu 1' in prompt  # only the last four messages
    assert '\n## Tổng quan' not in prompt  # each message is flattened to one line
    assert len(prompt) < 4 * (REWRITE_MESSAGE_CHARS + 20) + 100


@pytest.mark.parametrize('reply,expected', [
    ('Nhân viên thử việc có được nghỉ phép không?', 'Nhân viên thử việc có được nghỉ phép không?'),
    ('"Câu hỏi đã viết lại: Nhân viên thử việc có được nghỉ phép không?"', 'Nhân viên thử việc có được nghỉ phép không?'),
    ('Câu hỏi: Lương thử việc là bao nhiêu?', 'Lương thử việc là bao nhiêu?'),
    ('', None), ('   ', None),
    ('Nhân viên được nghỉ 12 ngày [1].', None),
    ('## Tổng quan\nQuy chế nội bộ.', None),
    ('- ý một', None),
    ('Người dùng: Tóm tắt tài liệu. Trợ lý: ' + 'Tổng quan quy chế. ' * 40, None),
])
def test_valid_rewrite(reply, expected):
    assert valid_rewrite(reply, 'Còn thử việc thì sao?') == expected


def test_bloated_rewrite_falls_back_to_the_original_question(service, db_session, db_user):
    add_document(db_session, 'library.pdf')
    bloated = 'Người dùng: Tóm tắt tài liệu. Trợ lý: ## Tổng quan ' + 'Quy chế lương, tóm tắt các ý chính. ' * 20

    def reply(body):
        return bloated if is_rewrite(body) else 'Không [1].'
    chat, fake, _ = service(reply, [chunk_hit('Thử việc không được nghỉ phép.')])
    first = run(chat, 'Nghỉ phép bao nhiêu ngày?', db_user)
    conversation_id = uuid.UUID(first['meta'][0]['conversation_id'])
    second = run(chat, 'Còn thử việc thì sao?', db_user, conversation_id=conversation_id)
    assert second['meta'][1] == {'intent': 'qa', 'question': 'Còn thử việc thì sao?'}
    assert answer_of(second) == 'Không [1].'
    rewrite_call = next(body for body in fake.chats if is_rewrite(body))
    assert rewrite_call['max_tokens'] == 80


@pytest.mark.parametrize('failure', [httpx.ReadTimeout('slow'), httpx.ConnectError('refused')])
def test_failed_rewrite_keeps_the_turn_alive(service, db_session, db_user, failure):
    add_document(db_session, 'library.pdf')
    def reply(body):
        if is_rewrite(body):
            raise failure
        return 'Có [1].'
    chat, fake, _ = service(reply, [chunk_hit('Thử việc được nghỉ phép.')])
    first = run(chat, 'Nghỉ phép bao nhiêu ngày?', db_user)
    second = run(chat, 'Còn thử việc thì sao?', db_user,
                 conversation_id=uuid.UUID(first['meta'][0]['conversation_id']))
    assert 'error' not in second and second['meta'][1]['question'] == 'Còn thử việc thì sao?'
    assert 'CÂU HỎI: Còn thử việc thì sao?' in user_message(fake.chats[-1])


def test_empty_rewrite_falls_back(service, db_session, db_user):
    add_document(db_session, 'library.pdf')
    def reply(body):
        return '""' if is_rewrite(body) else 'Có [1].'
    chat, _, _ = service(reply, [chunk_hit('x')])
    first = run(chat, 'Nghỉ phép bao nhiêu ngày?', db_user)
    second = run(chat, 'Còn thử việc?', db_user, conversation_id=uuid.UUID(first['meta'][0]['conversation_id']))
    assert second['meta'][1]['question'] == 'Còn thử việc?' and 'done' in second


# --- #2: no pooled connection while the LLM generates ------------------------------------------------------------

def test_no_connection_is_checked_out_while_the_llm_generates(service, db_engine, db_session, db_user):
    document = add_document(db_session, 'quy_che.pdf', [f'Chương {i}. ' + 'Quy định chi tiết. ' * 90 for i in range(4)])
    db_session.commit()
    checked_out = []

    def reply(body):
        checked_out.append(db_engine.pool.checkedout())
        return '## Tổng quan\nTóm tắt.' if not body['messages'][0]['content'].startswith('Bạn tóm tắt MỘT') else '- ý'
    chat, fake, _ = service(reply, [chunk_hit('Nghỉ phép 12 ngày.', document_id=str(document.id))],
                            llm_context_tokens=2048)
    rag = run(chat, 'Nghỉ phép bao nhiêu ngày?', db_user)
    follow = run(chat, 'Tóm tắt tài liệu quy_che.pdf', db_user,
                 conversation_id=uuid.UUID(rag['meta'][0]['conversation_id']))
    assert 'done' in rag and 'done' in follow and follow['meta'][1]['intent'] == 'summary'
    assert len(checked_out) == len(fake.chats) >= 4  # answer, rewrite, map calls, final summary
    assert set(checked_out) == {0}


def test_nfd_question_is_stored_in_nfc(service, db_session, db_user):
    chat, _, _ = service('Có [1].', [chunk_hit('x')])
    events = run(chat, unicodedata.normalize('NFD', 'Nghỉ phép bao nhiêu ngày?'), db_user)
    stored = db_session.query(Message).filter_by(
        conversation_id=uuid.UUID(events['meta'][0]['conversation_id']), role=MessageRole.user).one()
    assert stored.content == 'Nghỉ phép bao nhiêu ngày?'


# --- #8: section-scoped summary requests ---------------------------------------------------------------------------

@pytest.mark.parametrize('question,expected', [
    ('Tóm tắt Điều 5', True), ('tom tat dieu 5', True), ('Tóm tắt chương II của quy chế', True),
    ('Tóm tắt khoản 2 điều 3', True), ('Tóm tắt nội dung trang 4', True), ('Summarize section 3', True),
    ('Tóm tắt Mục 3.2', True),
    ('Tóm tắt tài liệu này', False), ('Tóm tắt các phần chính', False), ('Tóm tắt mục đích của quy chế', False),
    ('Tóm tắt các điều kiện nghỉ phép', False), ('Tóm tắt 5 điều quan trọng', False),
])
def test_names_section(question, expected):
    assert names_section(question) is expected


def test_section_summary_is_answered_with_cited_pages(service, db_session, db_user):
    document = add_document(db_session, 'quy_che.pdf', ['Điều 5. Nghỉ phép năm 12 ngày.'])
    chat, fake, store = service('Điều 5 quy định nghỉ phép năm 12 ngày [1].',
                                [chunk_hit('Điều 5. Nghỉ phép năm 12 ngày.', document_id=str(document.id), page=3)])
    events = run(chat, 'Tóm tắt Điều 5', db_user)
    assert events['meta'][1]['intent'] == 'qa' and 'progress' not in events
    assert events['done'][0]['sources'][0]['page'] == 3 and store.filters
    assert db_session.get(DocumentSummary, document.id) is None and len(fake.chats) == 1


# --- #9: multi-document summaries --------------------------------------------------------------------------------

def test_multi_document_summary_keeps_every_summary(service, db_session, db_user):
    documents = [add_document(db_session, f'tai_lieu_{i}.pdf', [f'Nội dung tài liệu {i}. ' * 20]) for i in range(3)]
    long_tail = 'Chi tiết. ' * 400  # ~4,000 characters per summary

    def reply(body):
        system = body['messages'][0]['content']
        if system.startswith('Bạn nhận bản tóm tắt của nhiều'):
            return 'Ba tài liệu cùng nói về quy chế.'
        name = FILENAME.search(system).group(1)
        return f'## Tổng quan\nBản tóm tắt của {name}.\n{long_tail}KẾT THÚC {name}'
    chat, fake, _ = service(reply, llm_context_tokens=4096)
    events = run(chat, 'Tóm tắt các tài liệu đã chọn', db_user, document_ids=[d.id for d in documents])
    answer = answer_of(events)
    assert answer.startswith('Ba tài liệu cùng nói về quy chế.')
    for document in documents:
        assert f'## {document.filename}\n\n### Tổng quan\nBản tóm tắt của {document.filename}.' in answer
        assert f'KẾT THÚC {document.filename}' in answer
    combine = fake.chats[-1]
    assert combine['stream'] is True and combine['max_tokens'] == MULTI_INTRO_TOKENS
    prompt_tokens = sum(len(m['content']) for m in combine['messages']) / CHARS_PER_TOKEN
    assert prompt_tokens + MULTI_INTRO_TOKENS <= 4096
    assert [s['document_id'] for s in events['done'][0]['sources']] == [str(d.id) for d in documents]


def test_demote_headings():
    assert demote_headings('## Tổng quan\ntext #1\n### Chương 1') == '### Tổng quan\ntext #1\n#### Chương 1'


# --- #35: final reduce prompt sized for its output -----------------------------------------------------------------

@pytest.mark.parametrize('context,max_tokens', [(8192, 2048), (2048, 2048), (8192, 1024)])
def test_final_prompt_plus_output_fits_the_context(db_session, context, max_tokens):
    # One section just under the map budget: without a separate final budget it would go straight to the reduce.
    settings = Settings(_env_file=None, llm_base_url=BASE_URL, llm_model=MODEL, llm_max_tokens=max_tokens)
    fake = FakeLLM(lambda body: '- ý' if body['messages'][0]['content'].startswith('Bạn tóm tắt MỘT') else 'Kết quả.')
    summarizer = SummaryService(db_session, LLMClient(settings, transport=httpx.MockTransport(fake)), context)
    document = add_document(db_session, 'dai.pdf', ['Quy định. ' * int(summarizer.budget_chars / 10 - 20)])
    events = list(summarizer.summarize(document, document.chunks))
    assert ''.join(e.data for e in events if e.kind == 'token') == 'Kết quả.'
    final = fake.chats[-1]
    assert final['max_tokens'] == min(max_tokens, context // 4)
    prompt_tokens = sum(len(m['content']) for m in final['messages']) / CHARS_PER_TOKEN
    assert prompt_tokens + final['max_tokens'] <= context


# --- #15: cache writes are best-effort upserts ---------------------------------------------------------------------

def _summarizer(session, reply='## Tổng quan\nKết quả.') -> SummaryService:
    settings = Settings(_env_file=None, llm_base_url=BASE_URL, llm_model=MODEL)
    return SummaryService(session, LLMClient(settings, transport=httpx.MockTransport(FakeLLM(reply))), 8192)


def test_concurrent_first_summaries_do_not_collide(db_engine, db_session):
    document = add_document(db_session, 'chung.pdf', ['Nội dung.'])
    factory = sessionmaker(bind=db_engine, expire_on_commit=False)
    with factory() as first_session, factory() as second_session:
        first, second = _summarizer(first_session, 'Bản A'), _summarizer(second_session, 'Bản B')
        # Both miss the cache before either stores its summary.
        runs = [first.summarize(document, list(document.chunks)), second.summarize(document, list(document.chunks))]
        assert [next(run).data['stage'] for run in runs] == ['reduce', 'reduce']
        texts = [''.join(e.data for e in run if e.kind == 'token') for run in runs]
    assert texts == ['Bản A', 'Bản B']
    db_session.expire_all()
    assert db_session.get(DocumentSummary, document.id).content == 'Bản B'


def test_document_deleted_during_summary_still_yields_the_summary(db_engine, db_session):
    document = add_document(db_session, 'xoa.pdf', ['Nội dung.'])
    document_id = document.id
    chunks = list(document.chunks)
    db_session.commit()
    with sessionmaker(bind=db_engine, expire_on_commit=False)() as session:
        run = _summarizer(session).summarize(document, chunks)
        assert next(run).data['stage'] == 'reduce'
        with db_engine.begin() as connection:
            connection.execute(text('DELETE FROM documents WHERE id = :id'), {'id': document_id})
        assert ''.join(e.data for e in run if e.kind == 'token') == '## Tổng quan\nKết quả.'
    db_session.expire_all()
    assert db_session.get(DocumentSummary, document_id) is None


# --- #39 / #38: filename matching ----------------------------------------------------------------------------------

@pytest.mark.parametrize('question,filename,expected', [
    ('Tóm tắt điều 1 về nghỉ phép', '1.pdf', 0),
    ('Tóm tắt tài liệu về nghỉ phép', 'A.pdf', 0),
    ('Tóm tắt tài liệu hdsd', 'HD.pdf', 0),
    ('Tóm tắt bất kỳ', '.pdf', 0),
    ('Tóm tắt quy_che_2.pdf', 'quy_che.pdf', 0),
    ('Tóm tắt quy_che.pdf giúp tôi', 'quy_che.pdf', len('quy_che.pdf')),
    ('Tóm tắt quy_che', 'quy_che.pdf', len('quy_che')),
    ('Tóm tắt Quy chế lương', unicodedata.normalize('NFD', 'Quy chế lương.pdf'), len('quy chế lương')),
    (unicodedata.normalize('NFD', 'Tóm tắt quy chế lương.pdf'), 'Quy chế lương.pdf', len('quy chế lương.pdf')),
])
def test_mentioned_name(question, filename, expected):
    assert mentioned_name(question, filename) == expected


def test_short_filenames_fall_back_to_retrieval(service, db_session, db_user):
    add_document(db_session, '1.pdf')
    target = add_document(db_session, 'A.pdf')
    chat, _, store = service('x', [chunk_hit('Nghỉ phép.', document_id=str(target.id))])
    chosen = chat._summary_documents('Tóm tắt tài liệu về nghỉ phép năm 1', [], None, db_user)
    assert [d.id for d in chosen] == [target.id] and store.filters


def test_longest_named_document_comes_first(service, db_session, db_user):
    short = add_document(db_session, 'quy_che.pdf')
    long = add_document(db_session, 'quy_che_luong.pdf')
    chat, _, store = service('x')
    chosen = chat._summary_documents('So sánh quy_che_luong.pdf và quy_che.pdf', [], None, db_user)
    assert [d.id for d in chosen] == [long.id, short.id] and not store.filters
