"""Run before the API/worker start: apply database migrations, check storage/vector services, make sure a
session signing secret exists and optionally create the first admin account."""
import logging
import os
import secrets
import sys

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.models import UserRole
from app.db.repositories import UserRepository
from app.db.session import get_sessionmaker
from app.services.vector_store import VectorStore
from app.storage.s3 import S3Storage
from scripts.create_user import create_or_reset

logger = logging.getLogger('init_services')


def ensure_signing_secret(settings: Settings) -> None:
    """Without AUTH_SECRET in .env, generate one once into the shared secrets volume (mode 600)."""
    if settings.auth_secret.get_secret_value():
        return
    path = settings.auth_secret_file
    if path.is_file() and path.read_text(encoding='utf-8').strip():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as file:
        file.write(secrets.token_urlsafe(48))
    logger.info('session signing secret generated')


def ensure_first_admin() -> None:
    """INITIAL_ADMIN_USERNAME / INITIAL_ADMIN_PASSWORD create an admin only while no account exists."""
    username, password = os.environ.get('INITIAL_ADMIN_USERNAME'), os.environ.get('INITIAL_ADMIN_PASSWORD')
    with get_sessionmaker()() as session:
        if UserRepository(session).count():
            return
        if not username or not password:
            logger.warning('no user accounts yet: set INITIAL_ADMIN_USERNAME/INITIAL_ADMIN_PASSWORD in .env '
                           'or run python -m scripts.create_user <name> --admin')
            return
        try:
            create_or_reset(session, username, password, UserRole.admin)
        except ValueError as exc:
            # A bad value must not keep the whole stack down; the app starts without accounts.
            logger.error('INITIAL_ADMIN_USERNAME/INITIAL_ADMIN_PASSWORD rejected: %s', exc)
            return
    logger.info('first admin account created')


def check_database(settings: Settings) -> bool:
    """Connect once before migrating so a wrong password or host fails with an actionable message."""
    url = make_url(settings.database_url)
    engine = create_engine(url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            connection.execute(text('SELECT 1'))
    except OperationalError as exc:
        if 'password authentication failed' in str(exc):
            # The postgres image stores POSTGRES_PASSWORD only when the pg_data volume is first created.
            logger.error('PostgreSQL rejected the password for user %s. POSTGRES_PASSWORD in .env probably changed '
                         'after the first start; set it back, or change it in the database first: '
                         'docker compose exec postgres psql -U %s -d %s -c "ALTER USER %s PASSWORD \'<new>\'" '
                         '(see README, section .env).', url.username, url.username, url.database, url.username)
        else:
            logger.error('cannot connect to PostgreSQL at %s:%s/%s (%s); check DATABASE_URL / POSTGRES_* in .env',
                         url.host, url.port, url.database, type(exc.orig).__name__)
        return False
    finally:
        engine.dispose()
    return True


def main() -> int:
    configure_logging()
    settings = get_settings()
    if not check_database(settings):
        return 1
    config = Config('alembic.ini')
    config.attributes['configure_logger'] = False  # keep the JSON logging configured above
    command.upgrade(config, 'head')
    logger.info('database migrations applied')
    S3Storage.from_settings(settings).ping()
    logger.info('object storage reachable bucket=%s', settings.s3_bucket)
    VectorStore.from_settings(settings).ping()
    logger.info('vector store reachable')
    ensure_signing_secret(settings)
    settings.signing_secret()
    ensure_first_admin()
    return 0


if __name__ == '__main__':
    sys.exit(main())
