"""Test doubles for infrastructure that has no local equivalent."""
from collections.abc import Iterator

from app.core.errors import PipelineError


class MemoryObject:
    def __init__(self, data: bytes, content_type: str):
        self.data, self.content_type, self.size = data, content_type, len(data)

    def iter_bytes(self, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        for start in range(0, len(self.data), chunk_size):
            yield self.data[start:start + chunk_size]

    def close(self) -> None:
        pass


class InMemoryStorage:
    """Implements app.storage.base.Storage in memory."""

    def __init__(self):
        self.objects: dict[str, MemoryObject] = {}

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self.objects[key] = MemoryObject(data, content_type)

    def get(self, key: str) -> MemoryObject:
        if key not in self.objects:
            raise PipelineError('file_not_found', 'Không tìm thấy file gốc trong kho lưu trữ.', 404)
        return self.objects[key]

    def read(self, key: str) -> bytes:
        return self.get(key).data

    def delete(self, key: str) -> None:
        self.objects.pop(key, None)

    def presigned_url(self, key: str, expires_seconds: int = 3600) -> str:
        return f'memory://{key}?expires={expires_seconds}'

    def ping(self) -> None:
        pass
