import io
import urllib.request
import uuid

import boto3
import pytest
from botocore.config import Config
from botocore.response import StreamingBody
from botocore.stub import Stubber

from app.core.config import Settings
from app.core.errors import PipelineError
from app.storage.s3 import S3Storage


@pytest.fixture
def stubbed():
    client = boto3.client('s3', endpoint_url='http://s3.test', aws_access_key_id='k', aws_secret_access_key='s',
                          region_name='us-east-1',
                          config=Config(signature_version='s3v4', s3={'addressing_style': 'path'}))
    with Stubber(client) as stubber:
        yield S3Storage('http://s3.test', 'k', 's', 'bucket', client=client), stubber
        stubber.assert_no_pending_responses()


def test_put_get_read_delete(stubbed):
    storage, stubber = stubbed
    stubber.add_response('put_object', {}, {'Bucket': 'bucket', 'Key': 'documents/a.pdf', 'Body': b'%PDF',
                                            'ContentType': 'application/pdf'})
    body = StreamingBody(io.BytesIO(b'%PDF'), 4)
    stubber.add_response('get_object', {'Body': body, 'ContentType': 'application/pdf', 'ContentLength': 4},
                         {'Bucket': 'bucket', 'Key': 'documents/a.pdf'})
    stubber.add_response('delete_object', {}, {'Bucket': 'bucket', 'Key': 'documents/a.pdf'})
    storage.put('documents/a.pdf', b'%PDF', 'application/pdf')
    assert storage.read('documents/a.pdf') == b'%PDF'
    storage.delete('documents/a.pdf')


def test_missing_object_is_404_and_outage_is_503(stubbed):
    storage, stubber = stubbed
    stubber.add_client_error('get_object', service_error_code='NoSuchKey', http_status_code=404)
    stubber.add_client_error('put_object', service_error_code='ServiceUnavailable', http_status_code=503)
    stubber.add_client_error('head_bucket', service_error_code='404', http_status_code=404)
    with pytest.raises(PipelineError) as missing:
        storage.get('nope')
    assert (missing.value.code, missing.value.status_code) == ('file_not_found', 404)
    with pytest.raises(PipelineError) as outage:
        storage.put('k', b'x', 'text/plain')
    assert (outage.value.code, outage.value.status_code) == ('storage_failure', 503)
    with pytest.raises(PipelineError) as ping:
        storage.ping()
    assert ping.value.code == 'storage_failure'


def test_presigned_url_is_signed_for_the_object(stubbed):
    storage, _ = stubbed
    url = storage.presigned_url('documents/a b.pdf', expires_seconds=60)
    assert url.startswith('http://s3.test/bucket/documents/a%20b.pdf?')
    assert 'X-Amz-Expires=60' in url and 'X-Amz-Signature=' in url


def test_real_s3_roundtrip_on_scratch_bucket():
    """Runs against SeaweedFS inside docker compose; skipped when no S3 endpoint is reachable."""
    settings = Settings()
    if not settings.s3_access_key.get_secret_value():
        pytest.skip('S3 credentials not configured (run via docker compose)')
    storage = S3Storage.from_settings(settings)
    storage.bucket = f'{settings.s3_bucket.removesuffix("-scratch")}-scratch'
    try:
        storage.client.head_bucket(Bucket=storage.bucket)
    except Exception:
        try:
            storage.client.create_bucket(Bucket=storage.bucket)
        except Exception as exc:
            pytest.skip(f'S3 endpoint unavailable ({type(exc).__name__})')
    key = f'tests/{uuid.uuid4()}.txt'
    storage.put(key, 'tiếng Việt'.encode(), 'text/plain; charset=utf-8')
    stored = storage.get(key)
    assert stored.content_type.startswith('text/plain') and stored.size == len('tiếng Việt'.encode())
    assert b''.join(stored.iter_bytes()) == 'tiếng Việt'.encode()
    assert urllib.request.urlopen(storage.presigned_url(key, 60)).read() == 'tiếng Việt'.encode()
    storage.delete(key)
    with pytest.raises(PipelineError) as gone:
        storage.read(key)
    assert gone.value.status_code == 404
    storage.delete(key)  # idempotent
