"""Fixed-window request limits in Redis, shared by every API process.

Fails open: if Redis is unreachable the request is served (and a warning logged) rather than locking
everyone out; nginx `limit_req` still caps raw request rates per IP.
"""
import logging
import time

from fastapi import Request

from app.core.errors import PipelineError

logger = logging.getLogger(__name__)


def client_ip(request: Request) -> str:
    # nginx sets X-Real-IP. The API port is published on 127.0.0.1 only, so a spoofed header can only come
    # from the host itself (see README, Bảo mật).
    return request.headers.get('x-real-ip') or (request.client.host if request.client else 'unknown')


def rate_limited() -> PipelineError:
    return PipelineError('rate_limited', 'Quá nhiều yêu cầu; vui lòng thử lại sau ít phút.', 429)


class RateLimiter:
    def __init__(self, redis=None):
        self.redis = redis

    @staticmethod
    def _name(bucket: str, key: str, window_seconds: int) -> str:
        return f'ratelimit:{bucket}:{key}:{int(time.time() // window_seconds)}'

    def hit(self, bucket: str, key: str, window_seconds: int = 60) -> int | None:
        """Count one event in the current window; the new count, or None when Redis is unavailable."""
        if self.redis is None:
            return None
        name = self._name(bucket, key, window_seconds)
        try:
            pipeline = self.redis.pipeline()
            pipeline.incr(name)
            pipeline.expire(name, window_seconds)
            return int(pipeline.execute()[0])
        except Exception:
            logger.warning('rate limiter unavailable bucket=%s', bucket)
            return None

    def blocked(self, bucket: str, key: str, limit: int, window_seconds: int = 60) -> bool:
        """Whether `limit` events were already counted in this window (without counting this request)."""
        if limit <= 0 or self.redis is None:
            return False
        try:
            count = self.redis.get(self._name(bucket, key, window_seconds))
        except Exception:
            logger.warning('rate limiter unavailable bucket=%s', bucket)
            return False
        return count is not None and int(count) >= limit

    def check(self, bucket: str, key: str, limit: int, window_seconds: int = 60) -> None:
        """Count this request and refuse it (429) beyond `limit` per window."""
        if limit <= 0:
            return
        count = self.hit(bucket, key, window_seconds)
        if count is not None and count > limit:
            logger.warning('rate limited bucket=%s', bucket)
            raise rate_limited()
