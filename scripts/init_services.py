"""Run before the API/worker start: apply database migrations and check storage/vector services."""
import logging
import sys

from alembic import command
from alembic.config import Config

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.services.vector_store import VectorStore
from app.storage.s3 import S3Storage

logger = logging.getLogger('init_services')


def main() -> int:
    configure_logging()
    settings = get_settings()
    command.upgrade(Config('alembic.ini'), 'head')
    logger.info('database migrations applied')
    S3Storage.from_settings(settings).ping()
    logger.info('object storage reachable bucket=%s', settings.s3_bucket)
    VectorStore.from_settings(settings).ping()
    logger.info('vector store reachable')
    return 0


if __name__ == '__main__':
    sys.exit(main())
