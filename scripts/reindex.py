"""Re-index stored documents with the current EMBEDDING_MODEL / CHUNK_* / OCR configuration.

Reads every original file from object storage, re-extracts, re-chunks and re-embeds it in this process
(like the worker would), replacing its chunk rows and vectors. A new embedding/chunk configuration writes
to a new Chroma collection; --drop-other-collections deletes the collections of old configurations.
Stop the worker first so it does not pick up the same documents:
  docker compose stop worker
  docker compose run --rm api python -m scripts.reindex [--document-id <uuid> ...] [--drop-other-collections]
  docker compose start worker
"""
import argparse
import json
import sys
import time
import uuid

from app.core.logging import configure_logging


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--document-id', type=uuid.UUID, action='append', default=[])
    parser.add_argument('--drop-other-collections', action='store_true')
    args = parser.parse_args()
    configure_logging()
    from app.core.dependencies import get_services, get_storage
    from app.core.errors import PipelineError
    from app.db.models import Document, IngestJob
    from app.db.session import get_sessionmaker
    from app.services.documents import DocumentService
    from app.services.jobs import IngestRunner, InlineJobQueue
    ingestion, _ = get_services()
    storage = get_storage()
    queue = InlineJobQueue(IngestRunner(get_sessionmaker(), storage, ingestion))
    report = {'reindexed': [], 'failed': [], 'dropped_collections': []}
    with get_sessionmaker()() as session:
        service = DocumentService(session, storage, ingestion.store, queue)
        query = session.query(Document).order_by(Document.created_at)
        if args.document_id:
            query = query.filter(Document.id.in_(args.document_id))
        for document in query.all():
            start = time.perf_counter()
            try:
                job = service.reindex(document)
                session.expire_all()
                service.raise_for_failure(session.get(IngestJob, job.id))
                session.refresh(document)
                report['reindexed'].append({'filename': document.filename, 'chunks': document.chunk_count,
                                            'seconds': round(time.perf_counter() - start, 1)})
            except PipelineError as exc:
                report['failed'].append({'filename': document.filename, 'error': exc.code})
    if args.drop_other_collections and not report['failed']:
        client, current = ingestion.store.client, ingestion.store.collection.name
        for collection in client.list_collections():
            if collection.name.startswith('documents_') and collection.name != current:
                client.delete_collection(collection.name)
                report['dropped_collections'].append(collection.name)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report['failed'] else 0


if __name__ == '__main__':
    sys.exit(main())
