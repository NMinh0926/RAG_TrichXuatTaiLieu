import json
import logging
import traceback
from contextvars import ContextVar
from datetime import datetime, timezone

# Set per HTTP request by the middleware in app.main; '-' outside requests (worker, scripts).
request_id: ContextVar[str] = ContextVar('request_id', default='-')
STANDARD_ATTRIBUTES = set(logging.LogRecord('', 0, '', 0, '', None, None).__dict__) | {'message', 'asctime'}


class JsonFormatter(logging.Formatter):
    """One JSON object per line: time, level, logger, message, request_id and any `extra={...}` fields."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {'time': datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec='milliseconds'),
                 'level': record.levelname, 'logger': record.name, 'message': record.getMessage(),
                 'request_id': request_id.get()}
        entry.update({key: value for key, value in record.__dict__.items() if key not in STANDARD_ATTRIBUTES})
        if record.exc_info:
            entry['exception'] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


def configure_logging(log_format: str | None = None):
    if log_format is None:
        from app.core.config import get_settings
        log_format = get_settings().log_format
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter() if log_format == 'json'
                         else logging.Formatter('%(asctime)s %(levelname)s %(name)s %(message)s'))
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    # Per-request HTTP client logs are noise; LLM errors are logged by the client. Access logs come from
    # the request middleware (with request id and duration) instead of uvicorn.
    for name in ('httpx', 'httpcore', 'uvicorn.access'):
        logging.getLogger(name).setLevel(logging.CRITICAL)


def log_failure(logger, code, exc):
    """Log stack locations, never exception payloads/locals with keys or documents."""
    frames = []
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        frames.extend(f'  File {frame.filename!r}, line {frame.lineno}, in {frame.name}\n'
                      for frame in traceback.extract_tb(exc.__traceback__))
        exc = exc.__cause__
    logger.error('failure=%s\n%s', code, ''.join(frames))
