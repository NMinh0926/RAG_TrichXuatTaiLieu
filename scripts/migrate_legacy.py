"""Move the pre-database layout (./data/uploads + persistent ./data/chroma) into storage + PostgreSQL + Chroma.

For each file in the uploads directory:
- skip it when PostgreSQL already has a document with the same SHA-256 (the script is re-runnable);
- link: the legacy Chroma document id was sha256(filename + NUL + bytes); when it matches a legacy
  collection built with the CURRENT embedding/chunk configuration, the original filename is recovered
  and the existing vectors are copied (new chunk ids, new document id) without re-running OCR/embedding;
- otherwise re-index the file through the normal upload path.
Legacy chunks carry no character offsets or bounding boxes; re-index later to get them.

Run (legacy data mounted read-only):
  docker compose run --rm -v "${PWD}/data:/legacy:ro" api python -m scripts.migrate_legacy \
      --uploads /legacy/uploads --chroma /legacy/chroma
"""
import argparse
import hashlib
import json
import logging
import shutil
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from app.core.logging import configure_logging

logger = logging.getLogger('migrate_legacy')


@dataclass
class LegacyDocument:
    filename: str
    ids: list[str] = field(default_factory=list)
    contents: list[str] = field(default_factory=list)
    metadatas: list[dict] = field(default_factory=list)
    embeddings: list[list[float]] = field(default_factory=list)


def read_legacy(path: Path, collection_name: str) -> dict[str, LegacyDocument]:
    """Legacy documents keyed by their old id, from the collection matching the current configuration.

    Chroma opens SQLite read-write, so a temporary copy is read and the legacy directory is never modified.
    """
    import chromadb
    from chromadb.config import Settings as ChromaSettings
    with tempfile.TemporaryDirectory() as copy:
        shutil.copytree(path, copy, dirs_exist_ok=True)
        client = chromadb.PersistentClient(path=copy, settings=ChromaSettings(anonymized_telemetry=False))
        names = [collection.name for collection in client.list_collections()]
        if collection_name not in names:
            logger.warning('no legacy collection %s (found %s); every file will be re-indexed', collection_name, names)
            return {}
        data = client.get_collection(collection_name).get(include=['documents', 'metadatas', 'embeddings'])
    documents: dict[str, LegacyDocument] = {}
    for key, text, meta, vector in zip(data['ids'], data['documents'], data['metadatas'], data['embeddings']):
        entry = documents.setdefault(meta['document_id'], LegacyDocument(meta['filename']))
        entry.ids.append(key)
        entry.contents.append(text)
        entry.metadatas.append(meta)
        entry.embeddings.append(list(vector))
    return documents


def _delete_row(session, document_id: uuid.UUID) -> None:
    from app.db.models import Document
    row = session.get(Document, document_id)
    if row is not None:
        session.delete(row)
        session.commit()


def legacy_id(filename: str, data: bytes) -> str:
    return hashlib.sha256(filename.encode('utf-8') + b'\0' + data).hexdigest()


def migrate(uploads: Path, chroma: Path) -> dict:
    from app.core.dependencies import get_services, get_storage
    from app.db.models import Chunk, Document, DocumentStatus, IngestJob
    from app.db.repositories import DocumentRepository
    from app.db.session import get_sessionmaker
    from app.models.schemas import Chunk as ChunkModel
    from app.services.documents import DocumentService
    from app.services.file_router import content_type_for, extension_of, SUPPORTED_EXTENSIONS
    from app.services.jobs import IngestRunner, InlineJobQueue
    ingestion, _ = get_services()
    store, storage = ingestion.store, get_storage()
    legacy = read_legacy(chroma, store.collection.name)
    known_names = {entry.filename for entry in legacy.values()}
    report = {'linked': [], 'reindexed': [], 'skipped_existing': [], 'failed': [],
              'legacy_documents_without_file': []}
    matched_legacy = set()
    runner = IngestRunner(get_sessionmaker(), storage, ingestion)
    with get_sessionmaker()() as session:
        repository = DocumentRepository(session)
        # Unlinkable files are processed right here, not by the shared worker.
        service = DocumentService(session, storage, store, InlineJobQueue(runner))
        for path in sorted(p for p in uploads.iterdir() if p.suffix.lower() in SUPPORTED_EXTENSIONS):
            data = path.read_bytes()
            match = next(((name, legacy_id(name, data)) for name in known_names
                          if legacy_id(name, data) in legacy), None)
            if match:
                matched_legacy.add(match[1])
            filename = match[0] if match else path.name
            existing = repository.get_by_sha256(hashlib.sha256(data).hexdigest())
            if existing is not None and existing.status == DocumentStatus.ready:
                report['skipped_existing'].append(filename)
                continue
            if existing is not None:
                service.delete(existing, force=True)  # left over by an interrupted run
            linked = None
            try:
                if match is None:
                    submission = service.submit(filename, data)
                    session.expire_all()
                    service.raise_for_failure(session.get(IngestJob, submission.job.id))
                    report['reindexed'].append(filename)
                    continue
                entry = legacy[match[1]]
                document_id = uuid.uuid4()
                document = Document(id=document_id, filename=filename, content_type=content_type_for(filename),
                                    size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest(),
                                    object_key=f'documents/{document_id}{extension_of(filename)}',
                                    status=DocumentStatus.processing, chunk_count=len(entry.ids),
                                    # Pre-account documents have no owner and are shared with every user.
                                    owner_id=None, is_shared=True,
                                    page_count=max((m.get('page') or 0 for m in entry.metadatas), default=0) or None)
                repository.add(document)
                # Commit before writing vectors: an interrupted run leaves a visible 'processing' row
                # that the next run replaces, never invisible orphan vectors.
                session.commit()
                linked = (document_id, document.object_key)
                storage.put(document.object_key, data, document.content_type)
                order = sorted(range(len(entry.ids)), key=lambda i: entry.metadatas[i].get('chunk_index', i))
                chunk_ids = [str(uuid.uuid4()) for _ in order]
                repository.replace_chunks(document_id, [
                    Chunk(id=uuid.UUID(chunk_ids[n]), document_id=document_id, ordinal=n,
                          page=entry.metadatas[i].get('page'), sheet=entry.metadatas[i].get('sheet'),
                          content=entry.contents[i], char_start=0, char_end=len(entry.contents[i]))
                    for n, i in enumerate(order)])
                chunks = [ChunkModel(content=entry.contents[i], chunk_index=n,
                                     metadata={k: v for k, v in entry.metadatas[i].items() if k != 'document_id'}
                                     | {'chunk_index': n})
                          for n, i in enumerate(order)]
                store.add(str(document_id), chunks, [entry.embeddings[i] for i in order], ids=chunk_ids)
                document.status = DocumentStatus.ready
                session.commit()
                report['linked'].append(filename)
            except Exception as exc:
                session.rollback()
                if linked is not None:
                    # Linked path: remove the claimed row, object and vectors.
                    linked_id, object_key = linked
                    for cleanup in (lambda: store.delete_document(str(linked_id)),
                                    lambda: storage.delete(object_key),
                                    lambda: _delete_row(session, linked_id)):
                        try:
                            cleanup()
                        except Exception:
                            session.rollback()
                            logger.warning('cleanup failed file=%s', path.name)
                logger.error('migration failed file=%s error=%s', path.name, type(exc).__name__)
                report['failed'].append({'file': path.name, 'error': getattr(exc, 'code', type(exc).__name__)})
    report['legacy_documents_without_file'] = sorted(
        entry.filename for key, entry in legacy.items() if key not in matched_legacy)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--uploads', type=Path, required=True)
    parser.add_argument('--chroma', type=Path, required=True)
    args = parser.parse_args()
    configure_logging()
    report = migrate(args.uploads, args.chroma)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report['failed'] else 0


if __name__ == '__main__':
    sys.exit(main())
