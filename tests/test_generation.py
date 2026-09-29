import json
import re

import httpx
import pytest

from app.core.config import Settings
from app.core.errors import PipelineError
from app.models.schemas import SearchHit
from app.services.answering import answer_from_contexts
from app.services.generation import GenerationService, cited_indexes
from app.services.ollama_client import OllamaClient
from app.services.prompts import INSUFFICIENT_ANSWER, NO_CONTEXT_ANSWER, SYSTEM_PROMPT, build_user_message

BASE_URL = 'http://ollama.test:11434'
MODEL = 'qwen2.5:7b'


def hit(text='Nhân viên được nghỉ phép 12 ngày mỗi năm.', **metadata):
    return SearchHit(content=text, metadata={'filename': 'policy.pdf', 'page': 1, **metadata},
                     relevance=0.9, distance=0.1)


class FakeOllama:
    """httpx transport double for Ollama. `reply` is a string or a function of the chat request body."""

    def __init__(self, reply=None, status=200, error=None, tags=(MODEL,)):
        self.reply, self.status, self.error, self.tags = reply, status, error, tags
        self.requests = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.error is not None:
            raise self.error
        body = json.loads(request.content) if request.content else None
        self.requests.append({'method': request.method, 'url': str(request.url), 'body': body})
        if request.url.path == '/api/tags':
            return httpx.Response(200, json={'models': [{'name': name} for name in self.tags]})
        if self.status != 200:
            return httpx.Response(self.status, json={'error': f'model "{MODEL}" not found, try pulling it first'})
        text = self.reply(body) if callable(self.reply) else self.reply
        return httpx.Response(200, json={'model': MODEL, 'message': {'role': 'assistant', 'content': text},
                                         'done': True})

    @property
    def chats(self) -> list[dict]:
        return [r['body'] for r in self.requests if r['url'].endswith('/api/chat')]


def service_with_reply(reply=None, *, status=200, error=None, tags=(MODEL,), **settings):
    fake = FakeOllama(reply, status, error, tags)
    config = Settings(_env_file=None, ollama_base_url=BASE_URL, ollama_model=MODEL, **settings)
    return GenerationService(config, OllamaClient(config, transport=httpx.MockTransport(fake))), fake


def user_message(body: dict) -> str:
    return body['messages'][1]['content']


def question_of(body: dict) -> str:
    return user_message(body).rsplit('CÂU HỎI: ', 1)[1]


def test_chat_request_format():
    service, fake = service_with_reply('12 ngày [Nguồn: policy.pdf, trang 1]')
    context = hit(document_id='private-id')
    service.answer('Nghỉ bao nhiêu ngày?', [context])
    request = fake.requests[0]
    assert request['method'] == 'POST' and request['url'] == f'{BASE_URL}/api/chat'
    body = request['body']
    assert body['model'] == MODEL and body['stream'] is False
    assert body['options'] == {'temperature': 0.2, 'num_ctx': 8192}
    assert body['messages'][0] == {'role': 'system', 'content': SYSTEM_PROMPT}
    assert user_message(body) == ('NGỮ CẢNH:\n[1] (policy.pdf, trang 1) Nhân viên được nghỉ phép 12 ngày mỗi năm.\n'
                                  'CÂU HỎI: Nghỉ bao nhiêu ngày?')
    assert 'private-id' not in json.dumps(body, ensure_ascii=False)


def test_user_message_location_without_page():
    message = build_user_message('q', [hit('a', page=None, sheet='Sales', filename='data.xlsx'),
                                       hit('b', page=None, filename='hr.docx')])
    assert '[1] (data.xlsx, sheet Sales) a' in message and '[2] (hr.docx) b' in message


def test_success_returns_answer_with_cited_sources():
    service, _ = service_with_reply('Nhân viên được nghỉ 12 ngày [Nguồn: policy.pdf, trang 1].')
    result = answer_from_contexts('q', [hit(), hit('Khác', filename='other.pdf', page=3)], service)
    assert result.answer == 'Nhân viên được nghỉ 12 ngày [Nguồn: policy.pdf, trang 1].'
    assert [s.model_dump(exclude_none=True) for s in result.sources] == [{'filename': 'policy.pdf', 'page': 1}]


def test_insufficient_context_discards_sources():
    service, fake = service_with_reply(INSUFFICIENT_ANSWER)
    result = answer_from_contexts('CEO của Microsoft là ai?', [hit()], service)
    assert result.answer == INSUFFICIENT_ANSWER and result.sources == []
    assert len(fake.chats) == 1


def test_empty_context_never_calls_llm():
    service, fake = service_with_reply('never')
    assert answer_from_contexts('q', [], service).answer == NO_CONTEXT_ANSWER
    assert fake.requests == []


def test_cited_indexes_matching():
    contexts = [hit(), hit(page=2), hit(filename='data.xlsx', page=None, sheet='Sales'), hit(filename='hr.docx', page=None)]
    answer = ('A [Nguồn: policy.pdf, trang 2]. B [Nguồn: data.xlsx, sheet Sales]. C [nguồn: hr]. '
              'D [Nguồn: unknown.pdf, trang 1]. E [Nguồn: policy.pdf, trang 9].')
    assert cited_indexes(answer, contexts) == [1, 2, 3]


def test_supported_without_valid_citation_has_no_sources():
    service, _ = service_with_reply('12 ngày.')
    result = answer_from_contexts('q', [hit(), hit(filename='other.pdf')], service)
    assert result.answer == '12 ngày.' and result.sources == []


def test_sources_are_deduplicated_retrieval_metadata():
    service, _ = service_with_reply('x [Nguồn: policy.pdf, trang 1] y [Nguồn: policy.pdf, trang 2]')
    result = answer_from_contexts('q', [hit(), hit(), hit(page=2)], service)
    assert [s.model_dump(exclude_none=True) for s in result.sources] == [
        {'filename': 'policy.pdf', 'page': 1}, {'filename': 'policy.pdf', 'page': 2}]


def test_ollama_not_running():
    service, _ = service_with_reply(error=httpx.ConnectError('connection refused'))
    with pytest.raises(PipelineError) as caught:
        service.answer('q', [hit()])
    assert caught.value.code == 'ollama_unavailable' and caught.value.status_code == 503
    assert str(caught.value) == f'Không kết nối được Ollama tại {BASE_URL}. Hãy kiểm tra Ollama đang chạy.'


def test_model_not_pulled():
    service, _ = service_with_reply(status=404)
    with pytest.raises(PipelineError) as caught:
        service.answer('q', [hit()])
    assert caught.value.code == 'ollama_model_missing' and caught.value.status_code == 503
    assert str(caught.value) == f'Model {MODEL} chưa được tải. Chạy: ollama pull {MODEL}'


def test_timeout():
    service, _ = service_with_reply(error=httpx.ReadTimeout('slow'))
    with pytest.raises(PipelineError) as caught:
        service.answer('q', [hit()])
    assert caught.value.code == 'ollama_timeout' and caught.value.status_code == 504
    assert 'OLLAMA_TIMEOUT_SECONDS' in str(caught.value) and '120' in str(caught.value)


@pytest.mark.parametrize('reply,status,code', [('x', 500, 'ollama_error'), ('  ', 200, 'ollama_empty_response')])
def test_other_failures(reply, status, code):
    service, _ = service_with_reply(reply, status=status)
    with pytest.raises(PipelineError) as caught:
        service.answer('q', [hit()])
    assert caught.value.code == code and caught.value.status_code == 502


def test_invalid_json_response():
    config = Settings(_env_file=None, ollama_base_url=BASE_URL, ollama_model=MODEL)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text='not json'))
    with pytest.raises(PipelineError) as caught:
        GenerationService(config, OllamaClient(config, transport=transport)).answer('q', [hit()])
    assert caught.value.code == 'ollama_invalid_response'


def test_timeout_and_base_url_from_settings():
    config = Settings(_env_file=None, ollama_base_url=BASE_URL + '/', ollama_timeout_seconds=45)
    client = OllamaClient(config)._client()
    assert str(client.base_url).rstrip('/') == BASE_URL and client.timeout.read == 45


@pytest.mark.parametrize('model,tags', [(MODEL, (MODEL, 'llama3:8b')), ('qwen2.5', ('qwen2.5:latest',))])
def test_health_model_available(model, tags):
    service, fake = service_with_reply(tags=tags)
    service.client.model = model
    result = service.client.health()
    assert result['status'] == 'ok' and result['model_available'] is True
    assert fake.requests[0]['method'] == 'GET' and fake.requests[0]['url'] == f'{BASE_URL}/api/tags'


def test_health_model_not_pulled():
    service, _ = service_with_reply(tags=('llama3:8b',))
    with pytest.raises(PipelineError) as caught:
        service.client.health()
    assert caught.value.code == 'ollama_model_missing'


def grounded_reply(question: str, keyword: str, outside: str = 'CEO của Microsoft là ai?'):
    """Fake grounded model: cites the context block containing `keyword`; insufficient for `outside`."""
    def reply(body: dict) -> str:
        if question_of(body) == outside:
            return INSUFFICIENT_ANSWER
        assert question_of(body) == question
        context = user_message(body).removeprefix('NGỮ CẢNH:\n').rsplit('\nCÂU HỎI: ', 1)[0]
        blocks = re.split(r'\n(?=\[\d+\] \()', context)
        block = next(block for block in blocks if keyword in block)
        return f"{keyword} [Nguồn: {block.split('(', 1)[1].split(') ', 1)[0]}]"
    return reply


@pytest.mark.parametrize('answer,expected', [
    ('A [Nguồn: quy_che.pdf, trang 1, 2]', [0, 1]),
    ('A [Nguồn: quy_che.pdf, trang 1-2]', [0, 1]),
    ('A [Nguồn: quy_che.pdf, trang 1 và 2]', [0, 1]),
    ('A [Nguồn: quy_che.pdf, trang 1; quy_che.pdf, trang 2]', [0, 1]),
    ('A (Nguồn: quy_che.pdf, trang 2).', [1]),
    ('A [Nguồn: quy_che.pdf trang 2]', [1]),
    ('A [Nguồn: "quy_che.pdf", trang: 2]', [1]),
    ('A [2]', [1]),
    ('A [Nguồn: Báo cáo [2024].pdf, trang 3]. B', [2]),
    ('A [Nguồn: quy_che.pdf, trang 2]. Xem thêm data.xlsx.', [1]),
    ('Theo data.xlsx, doanh thu là 150.', [3]),
    ('A [Nguồn: data.xlsx, sheet Other]', [3]),  # wrong sheet: falls back to the named file
    ('A [9]', []),
])
def test_citation_variants(answer, expected):
    contexts = [hit(filename='quy_che.pdf', page=1), hit(filename='quy_che.pdf', page=2),
                hit(filename='Báo cáo [2024].pdf', page=3), hit(filename='data.xlsx', page=None, sheet='Sales')]
    assert cited_indexes(answer, contexts) == expected


def test_partial_answer_with_citation_is_kept():
    text = ('Nhân viên được nghỉ 12 ngày [Nguồn: policy.pdf, trang 1]. '
            'Tài liệu không có đủ thông tin để trả lời câu hỏi này về phụ cấp.')
    service, _ = service_with_reply(text)
    verdict = service.answer('Nghỉ bao nhiêu ngày và phụ cấp bao nhiêu?', [hit()])
    assert verdict.supported and verdict.answer == text and verdict.context_indexes == [0]


def test_insufficient_with_punctuation_variant():
    service, _ = service_with_reply('"Tài liệu không có đủ thông tin để trả lời câu hỏi này"')
    assert service.answer('q', [hit()]).answer == INSUFFICIENT_ANSWER


@pytest.mark.parametrize('error', [httpx.ConnectTimeout('no route'), httpx.PoolTimeout('pool')])
def test_connect_timeout_is_unreachable_not_slow_model(error):
    service, _ = service_with_reply(error=error)
    with pytest.raises(PipelineError) as caught:
        service.answer('q', [hit()])
    assert caught.value.code == 'ollama_unavailable'


@pytest.mark.parametrize('payload', [{'message': 'hi'}, ['x'], {'message': {'content': 5}}])
def test_unexpected_chat_shapes_are_controlled_errors(payload):
    config = Settings(_env_file=None, ollama_base_url=BASE_URL, ollama_model=MODEL)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    with pytest.raises(PipelineError) as caught:
        GenerationService(config, OllamaClient(config, transport=transport)).answer('q', [hit()])
    assert caught.value.code == 'ollama_empty_response'


@pytest.mark.parametrize('payload', [['x'], {'models': None}])
def test_unexpected_tags_shapes_are_controlled_errors(payload):
    config = Settings(_env_file=None, ollama_base_url=BASE_URL, ollama_model=MODEL)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    with pytest.raises(PipelineError) as caught:
        OllamaClient(config, transport=transport).health()
    assert caught.value.code == 'ollama_invalid_response'


@pytest.mark.parametrize('text', [
    # Observed from qwen2.5:7b: refusal with a spurious citation must stay a refusal.
    'Tài liệu không có đủ thông tin để trả lời câu hỏi này. [Nguồn: sample.docx, sample.xlsx, sample.png]',
    'Tài liệu không có đủ thông tin để trả lời câu hỏi này [1].',
    'Xin lỗi, tài liệu không có đủ thông tin để trả lời câu hỏi này.',
])
def test_refusal_variants_have_no_sources(text):
    service, _ = service_with_reply(text)
    result = answer_from_contexts('q', [hit(), hit(filename='sample.docx', page=None)], service)
    assert result.answer == INSUFFICIENT_ANSWER and result.sources == []
