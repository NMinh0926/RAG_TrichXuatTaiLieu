"""OpenAI-compatible chat client (POST {LLM_BASE_URL}/chat/completions, GET {LLM_BASE_URL}/models).

Default target is Ollama's /v1 endpoint on the host; any OpenAI-compatible server works by configuration.
"""
import json
import logging
from collections.abc import Iterator

import httpx

from app.core.errors import PipelineError

logger = logging.getLogger(__name__)
Message = dict[str, str]


class LLMClient:
    def __init__(self, settings, transport: httpx.BaseTransport | None = None, model: str | None = None):
        self.settings = settings
        self.base_url = settings.llm_base_url.strip().rstrip('/')
        self.model = (model or settings.llm_model).strip()
        # transport is only injected by tests.
        self.transport = transport

    def _client(self) -> httpx.Client:
        seconds = self.settings.llm_timeout_seconds
        key = self.settings.llm_api_key.get_secret_value().strip()
        headers = {'Authorization': f'Bearer {key}'} if key else {}
        return httpx.Client(base_url=self.base_url, headers=headers, transport=self.transport,
                            timeout=httpx.Timeout(seconds, connect=min(10.0, seconds)))

    def _error(self, exc: Exception | None, response: httpx.Response | None, path: str) -> PipelineError:
        if isinstance(exc, (httpx.ConnectTimeout, httpx.PoolTimeout)) or (
                isinstance(exc, httpx.TransportError) and not isinstance(exc, httpx.TimeoutException)):
            logger.error('llm unreachable url=%s%s error=%s: %s', self.base_url, path, type(exc).__name__, exc)
            return PipelineError('llm_unavailable', f'Không kết nối được LLM tại {self.base_url}. '
                                 'Hãy kiểm tra Ollama (hoặc LLM server) đang chạy.', 503)
        if isinstance(exc, httpx.TimeoutException):
            logger.error('llm timeout url=%s%s model=%s timeout=%ss', self.base_url, path, self.model,
                         self.settings.llm_timeout_seconds)
            return PipelineError('llm_timeout', f'LLM phản hồi quá {self.settings.llm_timeout_seconds:g} giây. Hãy tăng '
                                 'LLM_TIMEOUT_SECONDS hoặc dùng model nhỏ hơn (ví dụ qwen2.5:3b).', 504)
        if exc is not None:
            logger.error('llm request failed url=%s%s error=%s: %s', self.base_url, path, type(exc).__name__, exc)
            return PipelineError('llm_error', 'Gọi LLM thất bại. Xem log server.', 502)
        status, body = response.status_code, response.text[:300]
        logger.error('llm http error url=%s%s model=%s status=%d body=%s', self.base_url, path, self.model, status, body)
        if status == 404:
            return PipelineError('llm_model_missing', f'Model {self.model} chưa được tải. Chạy: ollama pull {self.model}', 503)
        if status in (401, 403):
            return PipelineError('llm_auth_error', 'LLM từ chối xác thực; kiểm tra LLM_API_KEY.', 503)
        if status == 429:
            return PipelineError('llm_rate_limited', 'LLM đang quá tải hoặc hết hạn mức. Vui lòng thử lại sau.', 503)
        return PipelineError('llm_error', f'LLM trả lỗi HTTP {status}. Xem log server.', 502)

    def _body(self, messages: list[Message], max_tokens: int | None, stream: bool) -> dict:
        return {'model': self.model, 'messages': messages, 'stream': stream,
                'temperature': self.settings.llm_temperature,
                'max_tokens': max_tokens or self.settings.llm_max_tokens}

    def chat(self, messages: list[Message], max_tokens: int | None = None) -> str:
        path = '/chat/completions'
        try:
            with self._client() as client:
                response = client.post(path, json=self._body(messages, max_tokens, stream=False))
        except httpx.HTTPError as exc:
            raise self._error(exc, None, path) from exc
        if response.is_error:
            raise self._error(None, response, path)
        try:
            content = response.json()['choices'][0]['message']['content']
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            logger.error('llm invalid response shape model=%s', self.model)
            raise PipelineError('llm_invalid_response', 'LLM trả nội dung không hợp lệ.', 502) from exc
        if not isinstance(content, str) or not content.strip():
            raise PipelineError('llm_empty_response', 'LLM không trả nội dung trả lời.', 502)
        return content.strip()

    def stream(self, messages: list[Message], max_tokens: int | None = None) -> Iterator[str]:
        """Yield content deltas from an SSE stream (`data: {...}` lines, terminated by `data: [DONE]`)."""
        path = '/chat/completions'
        produced = False
        try:
            with self._client() as client, client.stream('POST', path, json=self._body(messages, max_tokens, True)) as response:
                if response.is_error:
                    response.read()
                    raise self._error(None, response, path)
                for line in response.iter_lines():
                    if not line.startswith('data:'):
                        continue
                    data = line[5:].strip()
                    if data == '[DONE]':
                        break
                    try:
                        delta = json.loads(data)['choices'][0].get('delta', {}).get('content') or ''
                    except (ValueError, KeyError, IndexError, TypeError) as exc:
                        raise PipelineError('llm_invalid_response', 'LLM trả nội dung không hợp lệ.', 502) from exc
                    if delta:
                        produced = True
                        yield delta
        except httpx.HTTPError as exc:
            raise self._error(exc, None, path) from exc
        if not produced:
            raise PipelineError('llm_empty_response', 'LLM không trả nội dung trả lời.', 502)

    def models(self) -> list[str]:
        path = '/models'
        try:
            with self._client() as client:
                response = client.get(path)
        except httpx.HTTPError as exc:
            raise self._error(exc, None, path) from exc
        if response.is_error:
            raise self._error(None, response, path)
        try:
            return [item['id'] for item in response.json()['data'] if isinstance(item.get('id'), str)]
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise PipelineError('llm_invalid_response', 'LLM trả nội dung không hợp lệ.', 502) from exc

    def health(self) -> dict:
        """Confirm the server answers and LLM_MODEL is available (for Ollama: pulled)."""
        models = self.models()
        wanted = {self.model, f'{self.model}:latest'} if ':' not in self.model else {self.model}
        if not wanted & set(models):
            logger.error('llm model not available model=%s available=%s', self.model, models)
            raise PipelineError('llm_model_missing', f'Model {self.model} chưa được tải. Chạy: ollama pull {self.model}', 503)
        return {'status': 'ok', 'base_url': self.base_url, 'model': self.model, 'model_available': True,
                'models': models}
