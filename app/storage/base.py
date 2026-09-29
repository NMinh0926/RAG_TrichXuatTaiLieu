from collections.abc import Iterator
from typing import Protocol


class StoredObject(Protocol):
    content_type: str
    size: int

    def iter_bytes(self, chunk_size: int = 1024 * 1024) -> Iterator[bytes]: ...

    def close(self) -> None: ...


class Storage(Protocol):
    """Object storage for original user files. The only place user files are persisted."""

    def put(self, key: str, data: bytes, content_type: str) -> None: ...

    def get(self, key: str) -> StoredObject: ...

    def read(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None: ...

    def presigned_url(self, key: str, expires_seconds: int = 3600) -> str: ...

    def ping(self) -> None: ...
