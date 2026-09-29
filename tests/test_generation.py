import json
import re

import httpx
import pytest

from app.core.config import Settings
from app.core.errors import PipelineError
from app.llm.client import LLMClient
from app.llm.prompts import INSUFFICIENT_ANSWER, NO_CONTEXT_ANSWER, RAG_SYSTEM, rag_messages
from app.models.schemas import SearchHit
from app.services.answering import answer_from_contexts
from app.services.generation import GenerationService, cited_numbers, is_insufficient

BASE_URL = 'http://llm.test:11434/v1'
MODEL = 'qwen2.5:7b'


def hit(text='Nhân viên được nghỉ phép 12 ngày mỗi năm.', **metadata):
    return SearchHit(id=metadata.pop('id', 'chunk-1'), content=text,
                     metadata={'filename': 'policy.pdf', 'page': 1, **metadata}, relevance=0.9, distance=0.1)


class FakeLLM:
    """httpx transport double for an OpenAI-compatible server.

    `reply` is a string or a function of the chat request body; streamed replies are split into deltas.
    """

    def __init__(self, reply=None, status=200, error=None, models=(MODEL,)):
        self.reply, self.status, self.error, self.models = reply, status, error, models
        self.requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.error is not None:
            raise self.error
        body = json.loads(request.content) if request.content else None
        self.requests.append({'method': request.method, 'url': str(request.url), 'body': body,
                              'auth': request.headers.get('authorization')})
        if request.url.path.endswith('/models'):
            return httpx.Response(200, json={'object': 'list', 'data': [{'id': name} for name in self.models]})
        if self.status != 200:
            return httpx.Response(self.status, json={'error': {'message': f'model "{MODEL}" not found'}})
        text = self.reply(body) if callable(self.reply) else self.reply
        if body.get('stream'):
            deltas = [text[i:i + 7] for i in range(0, len(text), 7)]
            lines = [f"data: {json.dumps({'choices': [{'delta': {'content': d}}]})}\n\n" for d in deltas]
            return httpx.Response(200, content=''.join(lines) + 'data: [DONE]\n\n',
                                  headers={'content-type': 'text/event-stream'})
        return httpx.Response(200, json={'choices': [{'message': {'role': 'assistant', 'content': text}}]})

    @property
    def chats(self) -> list[dict]:
        return [r['body'] for r in self.requests if r['url'].endswith('/chat/completions')]


def service_with_reply(reply=None, *, status=200, error=None, models=(MODEL,), **settings):
    fake = FakeLLM(reply, status, error, models)
    config = Settings(_env_file=None, llm_base_url=BASE_URL, llm_model=MODEL, **settings)
    return GenerationService(config, LLMClient(config, transport=httpx.MockTransport(fake))), fake


def user_message(body: dict) -> str:
    return body['messages'][-1]['content']


def question_of(body: dict) -> str:
    return user_message(body).rsplit('CÂU HỎI: ', 1)[1]


def grounded_reply(question: str, keyword: str, outside: str = 'CEO của Microsoft là ai?'):
    """Fake grounded model: cites the numbered context containing `keyword`; refuses for `outside`."""
    def reply(body: dict) -> str:
        if question_of(body) == outside:
            return INSUFFICIENT_ANSWER
        assert question_of(body) == question
        context = user_message(body).removeprefix('NGỮ CẢNH:\n').rsplit('\n\nCÂU HỎI: ', 1)[0]
        blocks = re.split(r'\n\n(?=\[\d+\] \()', context)
        number = next(int(block[1:block.index(']')]) for block in blocks if keyword in block)
        return f'{keyword} [{number}]'
    return reply


def test_chat_request_format():
    service, fake = service_with_reply('12 ngày [1]', llm_api_key='secret-token', llm_max_tokens=900)
    context = hit(document_id='private-id')
    service.answer('Nghỉ bao nhiêu ngày?', [context])
    request = fake.requests[0]
    assert request['method'] == 'POST' and request['url'] == f'{BASE_URL}/chat/completions'
    assert request['auth'] == 'Bearer secret-token'
    body = request['body']
    assert body['model'] == MODEL and body['stream'] is False
    assert body['temperature'] == 0.2 and body['max_tokens'] == 900
    assert body['messages'][0] == {'role': 'system', 'content': RAG_SYSTEM}
    assert user_message(body) == ('NGỮ CẢNH:\n[1] (policy.pdf, trang 1)\nNhân viên được nghỉ phép 12 ngày mỗi năm.'
                                  '\n\nCÂU HỎI: Nghỉ bao nhiêu ngày?')
    assert 'private-id' not in json.dumps(body, ensure_ascii=False)


def test_no_api_key_sends_no_authorization_header():
    service, fake = service_with_reply('x [1]')
    service.answer('q', [hit()])
    assert fake.requests[0]['auth'] is None


def test_context_locations_without_page():
    message = rag_messages('q', [hit('a', page=None, sheet='Sales', filename='data.xlsx'),
                                 hit('b', page=None, filename='hr.docx')])[1]['content']
    assert '[1] (data.xlsx, sheet Sales)\na' in message and '[2] (hr.docx)\nb' in message


def test_success_cites_only_referenced_contexts():
    service, _ = service_with_reply('Nghỉ 12 ngày [2]. Đơn vị: ngày [2][1].')
    result = answer_from_contexts('q', [hit('Khác', filename='other.pdf', page=3), hit()], service)
    assert result.answer == 'Nghỉ 12 ngày [2]. Đơn vị: ngày [2][1].'
    assert [s.model_dump(exclude_none=True) for s in result.sources] == [
        {'filename': 'policy.pdf', 'page': 1}, {'filename': 'other.pdf', 'page': 3}]


@pytest.mark.parametrize('answer,expected', [
    ('A [1]. B [3].', [1, 3]), ('A [2][1] [2]', [2, 1]), ('A [1, 3]', [1, 3]), ('A [1-3]', [1, 2, 3]),
    ('A [1–2]', [1, 2]), ('A [9]', []), ('A [0]', []), ('Năm [2024] tăng', []), ('Không có trích dẫn', []),
])
def test_cited_numbers(answer, expected):
    assert cited_numbers(answer, 3) == expected


@pytest.mark.parametrize('text,refusal', [
    (INSUFFICIENT_ANSWER, True), (f'"{INSUFFICIENT_ANSWER.rstrip(".")}"', True), (f'{INSUFFICIENT_ANSWER} [1]', True),
    (f'Xin lỗi, {INSUFFICIENT_ANSWER.lower()}', True),
    (f'Nghỉ 12 ngày [1]. {INSUFFICIENT_ANSWER} về phụ cấp.', False), ('Nghỉ 12 ngày [1].', False),
])
def test_refusal_detection(text, refusal):
    assert is_insufficient(text) is refusal


def test_insufficient_context_has_no_sources():
    service, fake = service_with_reply(f'{INSUFFICIENT_ANSWER} [1]')
    result = answer_from_contexts('CEO của Microsoft là ai?', [hit()], service)
    assert result.answer == INSUFFICIENT_ANSWER and result.sources == []
    assert len(fake.chats) == 1


def test_empty_context_never_calls_llm():
    service, fake = service_with_reply('never')
    assert answer_from_contexts('q', [], service).answer == NO_CONTEXT_ANSWER
    assert fake.requests == []


def test_stream_yields_deltas_that_finalize_to_the_answer():
    service, fake = service_with_reply('Nhân viên được nghỉ 12 ngày mỗi năm [1].')
    deltas = list(service.stream('q', [hit()]))
    assert len(deltas) > 1 and fake.chats[0]['stream'] is True
    verdict = service.finalize(''.join(deltas), [hit()])
    assert verdict.supported and verdict.answer == 'Nhân viên được nghỉ 12 ngày mỗi năm [1].'
    assert verdict.context_indexes == [0]


def test_llm_not_running():
    service, _ = service_with_reply(error=httpx.ConnectError('connection refused'))
    with pytest.raises(PipelineError) as caught:
        service.answer('q', [hit()])
    assert caught.value.code == 'llm_unavailable' and caught.value.status_code == 503
    assert BASE_URL in str(caught.value)


@pytest.mark.parametrize('call', ['answer', 'stream'])
def test_model_not_pulled(call):
    service, _ = service_with_reply(status=404)
    with pytest.raises(PipelineError) as caught:
        list(getattr(service, call)('q', [hit()])) if call == 'stream' else service.answer('q', [hit()])
    assert caught.value.code == 'llm_model_missing' and caught.value.status_code == 503
    assert str(caught.value) == f'Model {MODEL} chưa được tải. Chạy: ollama pull {MODEL}'


def test_timeout_and_connect_timeout_are_distinguished():
    slow, _ = service_with_reply(error=httpx.ReadTimeout('slow'))
    with pytest.raises(PipelineError) as caught:
        slow.answer('q', [hit()])
    assert caught.value.code == 'llm_timeout' and caught.value.status_code == 504
    assert 'LLM_TIMEOUT_SECONDS' in str(caught.value)
    unreachable, _ = service_with_reply(error=httpx.ConnectTimeout('no route'))
    with pytest.raises(PipelineError) as caught:
        unreachable.answer('q', [hit()])
    assert caught.value.code == 'llm_unavailable'


@pytest.mark.parametrize('status,code', [(401, 'llm_auth_error'), (429, 'llm_rate_limited'), (500, 'llm_error')])
def test_http_errors(status, code):
    service, _ = service_with_reply(status=status)
    with pytest.raises(PipelineError) as caught:
        service.answer('q', [hit()])
    assert caught.value.code == code


@pytest.mark.parametrize('payload,code', [({'choices': []}, 'llm_invalid_response'), ({'x': 1}, 'llm_invalid_response'),
                                          ({'choices': [{'message': {'content': '  '}}]}, 'llm_empty_response')])
def test_bad_response_shapes(payload, code):
    config = Settings(_env_file=None, llm_base_url=BASE_URL, llm_model=MODEL)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    with pytest.raises(PipelineError) as caught:
        GenerationService(config, LLMClient(config, transport=transport)).answer('q', [hit()])
    assert caught.value.code == code


def test_empty_stream_is_an_error():
    config = Settings(_env_file=None, llm_base_url=BASE_URL, llm_model=MODEL)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content='data: [DONE]\n\n'))
    with pytest.raises(PipelineError) as caught:
        list(LLMClient(config, transport=transport).stream([{'role': 'user', 'content': 'q'}]))
    assert caught.value.code == 'llm_empty_response'


@pytest.mark.parametrize('model,models', [(MODEL, (MODEL, 'llama3:8b')), ('qwen2.5', ('qwen2.5:latest',))])
def test_health_model_available(model, models):
    service, fake = service_with_reply(models=models)
    service.client.model = model
    assert service.client.health()['model_available'] is True
    assert fake.requests[0]['method'] == 'GET' and fake.requests[0]['url'] == f'{BASE_URL}/models'


def test_health_model_missing():
    service, _ = service_with_reply(models=('llama3:8b',))
    with pytest.raises(PipelineError) as caught:
        service.client.health()
    assert caught.value.code == 'llm_model_missing'
