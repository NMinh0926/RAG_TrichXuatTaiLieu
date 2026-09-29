"""Point the full app at scratch resources so diagnostics and benchmarks never touch user data.

Call `isolate()` BEFORE app settings are created, then `prepare()` once:
- Chroma: local persistent directory (not the shared Chroma server)
- PostgreSQL: the `<POSTGRES_DB>_scratch` database (created if missing), migrated to head. The automated
  tests use `<POSTGRES_DB>_test` and truncate it, so the two never interfere.
- Object storage: a `<S3_BUCKET>-scratch` bucket, created if missing
- Ingest jobs run inline in this process (the shared worker reads the real database).
"""
import os
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

SCRATCH_SUFFIX = '_scratch'


def scratch_database_url(database_url: str, suffix: str = SCRATCH_SUFFIX) -> str:
    url = make_url(database_url)
    base = url.database.removesuffix('_test').removesuffix(SCRATCH_SUFFIX)
    return url.set(database=f'{base}{suffix}').render_as_string(hide_password=False)


def isolate(work: Path) -> None:
    from app.core.config import Settings
    defaults = Settings()
    os.environ['CHROMA_HOST'] = ''
    os.environ['CHROMA_PATH'] = str(Path(work) / 'chroma')
    os.environ['DATABASE_URL'] = scratch_database_url(defaults.database_url)
    if not defaults.s3_bucket.endswith('-scratch'):
        os.environ['S3_BUCKET'] = f'{defaults.s3_bucket}-scratch'


def ensure_database(database_url: str) -> None:
    """CREATE DATABASE if missing (connects to the server's `postgres` maintenance database)."""
    url = make_url(database_url)
    engine = create_engine(url.set(database='postgres'), isolation_level='AUTOCOMMIT')
    try:
        with engine.connect() as connection:
            exists = connection.scalar(text('SELECT 1 FROM pg_database WHERE datname = :name'), {'name': url.database})
            if not exists:
                connection.execute(text(f'CREATE DATABASE "{url.database}"'))
    finally:
        engine.dispose()


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
    if not settings.database_url.endswith(SCRATCH_SUFFIX) or settings.chroma_host:
        raise RuntimeError('isolate() must run before prepare()')
    ensure_database(settings.database_url)
    config = Config('alembic.ini')
    config.set_main_option('sqlalchemy.url', settings.database_url.replace('%', '%%'))
    command.upgrade(config, 'head')
    client = get_storage().client
    try:
        client.head_bucket(Bucket=settings.s3_bucket)
    except ClientError:
        client.create_bucket(Bucket=settings.s3_bucket)
    # Process jobs in this process: the shared worker reads the real database, not the scratch one.
    from app.core import dependencies
    from app.services.jobs import IngestRunner, InlineJobQueue
    runner = IngestRunner(get_sessionmaker(), get_storage(), dependencies.get_services()[0])
    dependencies.get_job_queue = lambda: InlineJobQueue(runner)
