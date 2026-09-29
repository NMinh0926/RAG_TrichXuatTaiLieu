"""Point the full app at scratch resources so diagnostics, benchmarks and tests never touch user data.

Call `isolate()` BEFORE app settings are created, then `prepare()` once:
- Chroma: local persistent directory (not the shared Chroma server)
- PostgreSQL: the `<POSTGRES_DB>_test` database (created by docker/postgres/initdb), migrated to head
- Object storage: a `<S3_BUCKET>-scratch` bucket, created if missing
"""
import os
from pathlib import Path

from sqlalchemy.engine import make_url


def scratch_database_url(database_url: str) -> str:
    url = make_url(database_url)
    name = url.database if url.database.endswith('_test') else f'{url.database}_test'
    return url.set(database=name).render_as_string(hide_password=False)


def isolate(work: Path) -> None:
    from app.core.config import Settings
    defaults = Settings()
    os.environ['CHROMA_HOST'] = ''
    os.environ['CHROMA_PATH'] = str(Path(work) / 'chroma')
    os.environ['DATABASE_URL'] = scratch_database_url(defaults.database_url)
    if not defaults.s3_bucket.endswith('-scratch'):
        os.environ['S3_BUCKET'] = f'{defaults.s3_bucket}-scratch'


def prepare() -> None:
    from alembic import command
    from alembic.config import Config
    from botocore.exceptions import ClientError
    from app.core.config import get_settings
    from app.core.dependencies import get_storage
    from app.db.session import get_engine, get_sessionmaker
    for cached in (get_settings, get_engine, get_sessionmaker, get_storage):
        cached.cache_clear()
    settings = get_settings()
    if not settings.database_url.endswith('_test') or settings.chroma_host:
        raise RuntimeError('isolate() must run before prepare()')
    config = Config('alembic.ini')
    config.set_main_option('sqlalchemy.url', settings.database_url.replace('%', '%%'))
    command.upgrade(config, 'head')
    client = get_storage().client
    try:
        client.head_bucket(Bucket=settings.s3_bucket)
    except ClientError:
        client.create_bucket(Bucket=settings.s3_bucket)
