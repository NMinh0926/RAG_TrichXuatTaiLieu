"""Minimal client for a local Ollama server (POST /api/chat, GET /api/tags)."""
import logging

import httpx

from app.core.errors import PipelineError

logger = logging.getLogger(__name__)


class OllamaClient:
    def __init__(self, settings, transport: httpx.BaseTransport | None = None):
        self.settings = settings
        self.base_url = settings.ollama_base_url.strip().rstrip('/')
        self.model = settings.ollama_model.strip()
        # transport is only injected by tests.
        self.transport = transport

    def _client(self) -> httpx.Client:
        timeout = httpx.Timeout(self.settings.ollama_timeout_seconds, connect=min(10.0, self.settings.ollama_timeout_seconds))
        return httpx.Client(base_url=self.base_url, timeout=timeout, transport=self.transport)

    def _unavailable(self) -> PipelineError:
        return PipelineError('ollama_unavailable',
                             f'Không kết nối được Ollama tại {self.base_url}. Hãy kiểm tra Ollama đang chạy.', 503)

    def _model_missing(self) -> PipelineError:
        return PipelineError('ollama_model_missing',
                             f'Model {self.model} chưa được tải. Chạy: ollama pull {self.model}', 503)

    def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            with self._client() as client:
                response = client.request(method, path, **kwargs)
        except (httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
            # Nothing answered the connection: this is 'not reachable', not a slow model.
            logger.error('ollama connect timeout url=%s%s error=%s', self.base_url, path, type(exc).__name__)
            raise self._unavailable() from exc
        except httpx.TimeoutException as exc:
            logger.error('ollama timeout url=%s%s model=%s timeout=%ss error=%s', self.base_url, path, self.model,
                         self.settings.ollama_timeout_seconds, type(exc).__name__)
            raise PipelineError(
                'ollama_timeout',
                f'Ollama phản hồi quá {self.settings.ollama_timeout_seconds:g} giây. Hãy tăng OLLAMA_TIMEOUT_SECONDS '
                f'hoặc dùng model nhỏ hơn (ví dụ qwen2.5:3b).', 504) from exc
        except httpx.TransportError as exc:
            logger.error('ollama unreachable url=%s%s error=%s: %s', self.base_url, path, type(exc).__name__, exc)
            raise self._unavailable() from exc
        except httpx.HTTPError as exc:
            logger.error('ollama request failed url=%s%s error=%s: %s', self.base_url, path, type(exc).__name__, exc)
            raise PipelineError('ollama_error', 'Gọi Ollama thất bại. Xem log server.', 502) from exc
        # Ollama answers 404 on /api/chat when the model has not been pulled.
        if response.status_code == 404 and path == '/api/chat':
            logger.error('ollama 404 url=%s%s model=%s body=%s', self.base_url, path, self.model, response.text[:300])
            raise self._model_missing()
        if response.is_error:
            logger.error('ollama http error url=%s%s model=%s status=%d body=%s', self.base_url, path, self.model,
                         response.status_code, response.text[:300])
            raise PipelineError('ollama_error', f'Ollama trả lỗi HTTP {response.status_code}. Xem log server.', 502)
        try:
            return response.json()
        except ValueError as exc:
            logger.error('ollama invalid json url=%s%s status=%d', self.base_url, path, response.status_code)
            raise PipelineError('ollama_invalid_response', 'Ollama trả nội dung không hợp lệ.', 502) from exc

    def chat(self, system: str, user: str) -> str:
        body = {
            'model': self.model,
            'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
            'stream': False,
            'options': {'temperature': self.settings.ollama_temperature, 'num_ctx': self.settings.ollama_num_ctx},
        }
        data = self._request('POST', '/api/chat', json=body)
        message = data.get('message') if isinstance(data, dict) else None
        content = message.get('content') if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            logger.error('ollama empty response model=%s done_reason=%s', self.model,
                         data.get('done_reason') if isinstance(data, dict) else None)
            raise PipelineError('ollama_empty_response', 'Ollama không trả nội dung trả lời.', 502)
        return content.strip()

    def list_models(self) -> list[str]:
        data = self._request('GET', '/api/tags')
        models = data.get('models') if isinstance(data, dict) else None
        if not isinstance(models, list):
            logger.error('ollama /api/tags unexpected shape type=%s', type(models).__name__)
            raise PipelineError('ollama_invalid_response', 'Ollama trả nội dung không hợp lệ.', 502)
        return [item['name'] for item in models if isinstance(item, dict) and isinstance(item.get('name'), str)]

    def health(self) -> dict:
        """GET /api/tags and confirm OLLAMA_MODEL is pulled. Raises PipelineError otherwise."""
        models = self.list_models()
        # 'qwen2.5' means 'qwen2.5:latest' in Ollama.
        wanted = {self.model, f'{self.model}:latest'} if ':' not in self.model else {self.model}
        if not wanted & set(models):
            logger.error('ollama model not pulled model=%s available=%s', self.model, models)
            raise self._model_missing()
        return {'status': 'ok', 'base_url': self.base_url, 'model': self.model, 'model_available': True,
                'models': models}
