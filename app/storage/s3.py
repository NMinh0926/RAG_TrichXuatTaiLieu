"""S3-compatible storage (SeaweedFS in compose; AWS S3 or Cloudflare R2 by configuration only)."""
import logging
from collections.abc import Iterator

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.core.errors import PipelineError

logger = logging.getLogger(__name__)
MISSING_CODES = {'404', 'NoSuchKey', 'NotFound'}


class S3Object:
    def __init__(self, body, content_type: str, size: int):
        self._body, self.content_type, self.size = body, content_type, size

    def iter_bytes(self, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        try:
            yield from self._body.iter_chunks(chunk_size)
        finally:
            self.close()

    def close(self) -> None:
        self._body.close()


class S3Storage:
    def __init__(self, endpoint: str, access_key: str, secret_key: str, bucket: str, region: str = 'us-east-1',
                 client=None):
        self.bucket = bucket
        # Path-style addressing works for SeaweedFS/MinIO and R2; virtual-host style needs DNS per bucket.
        self.client = client or boto3.client(
            's3', endpoint_url=endpoint or None, aws_access_key_id=access_key or None,
            aws_secret_access_key=secret_key or None, region_name=region,
            config=Config(signature_version='s3v4', s3={'addressing_style': 'path'},
                          retries={'max_attempts': 3, 'mode': 'standard'}, connect_timeout=5, read_timeout=60))

    @classmethod
    def from_settings(cls, settings) -> 'S3Storage':
        return cls(settings.s3_endpoint, settings.s3_access_key.get_secret_value(),
                   settings.s3_secret_key.get_secret_value(), settings.s3_bucket, settings.s3_region)

    def _fail(self, action: str, key: str, exc: Exception) -> PipelineError:
        code = exc.response.get('Error', {}).get('Code') if isinstance(exc, ClientError) else type(exc).__name__
        logger.error('storage %s failed bucket=%s key=%s code=%s', action, self.bucket, key, code)
        if code in MISSING_CODES and action != 'ping':
            return PipelineError('file_not_found', 'Không tìm thấy file gốc trong kho lưu trữ.', 404)
        return PipelineError('storage_failure', 'Kho lưu trữ file không khả dụng. Vui lòng thử lại sau.', 503)

    def put(self, key: str, data: bytes, content_type: str) -> None:
        try:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
        except (BotoCoreError, ClientError) as exc:
            raise self._fail('put', key, exc) from exc

    def get(self, key: str) -> S3Object:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
        except (BotoCoreError, ClientError) as exc:
            raise self._fail('get', key, exc) from exc
        return S3Object(response['Body'], response.get('ContentType') or 'application/octet-stream',
                        int(response.get('ContentLength') or 0))

    def read(self, key: str) -> bytes:
        stored = self.get(key)
        try:
            return b''.join(stored.iter_bytes())
        except (BotoCoreError, ClientError) as exc:
            raise self._fail('read', key, exc) from exc

    def delete(self, key: str) -> None:
        # S3 delete is idempotent: a missing key is not an error.
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except (BotoCoreError, ClientError) as exc:
            raise self._fail('delete', key, exc) from exc

    def presigned_url(self, key: str, expires_seconds: int = 3600) -> str:
        return self.client.generate_presigned_url('get_object', Params={'Bucket': self.bucket, 'Key': key},
                                                  ExpiresIn=expires_seconds)

    def ping(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except (BotoCoreError, ClientError) as exc:
            raise self._fail('ping', self.bucket, exc) from exc
