"""Worker/job robustness: single execution per document, crash loops, stuck documents, stale summaries,
the non-blocking legacy upload and NFC filenames."""
import asyncio
import io
import unicodedata

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.api.documents import read_upload
from app.core.config import Settings
from app.core.errors import PipelineError
from app.db.models import Chunk, Document, DocumentStatus, DocumentSummary, IngestJob, JobStatus
from app.db.repositories import DocumentRepository
from app.services.documents import DocumentService
from app.services.jobs import IngestRunner, locked_documents, try_lock_document
from app.worker import MAX_WORKER_CRASHES, RESTART_WARNING, recover
from tests.test_worker import RecordingQueue, _job


class FailingQueue:
    def enqueue(self, job_id):
        raise PipelineError('queue_unavailable', 'Hàng đợi xử lý (Redis) không khả dụng.', 503)


class ExplodingIngestion:
    """Would fail loudly if a skipped job were processed."""
    store = None

    def process(self, *args, **kwargs):
        raise AssertionError('job must not run')


def test_a_document_is_processed_by_one_runner_at_a_time(db_session, db_engine):
    job = _job(db_session, JobStatus.queued)
    factory = sessionmaker(bind=db_engine)
    runner = IngestRunner(factory, storage=None, ingestion=ExplodingIngestion())
    with Session(db_engine) as other_worker:
        assert try_lock_document(other_worker, job.document_id)
        assert locked_documents(db_session, [job.document_id]) == {job.document_id}
        runner.run(job.id)  # delivered twice: the second delivery defers instead of running concurrently
        db_session.expire_all()
        assert db_session.get(IngestJob, job.id).status == JobStatus.queued
        # Recovery leaves a job whose document is being processed alone.
        queue = RecordingQueue()
        assert recover(factory, queue, waiting=lambda job_id: False) == 0 and queue.enqueued == []
    assert locked_documents(db_session, [job.document_id]) == set()
    # A job that is no longer queued (already done, superseded) is skipped.
    done = _job(db_session, JobStatus.succeeded, 'done', 100)
    runner.run(done.id)


def test_a_file_that_keeps_crashing_the_worker_is_failed_not_retried_forever(db_session, db_engine):
    job = _job(db_session, JobStatus.running)
    factory = sessionmaker(bind=db_engine)
    deleted = []
    for crash in range(1, MAX_WORKER_CRASHES + 1):
        queue = RecordingQueue()
        recover(factory, queue, waiting=lambda job_id: False, delete_vectors=deleted.append)
        db_session.expire_all()
        current = db_session.get(IngestJob, job.id)
        if crash < MAX_WORKER_CRASHES:
            assert queue.enqueued == [job.id] and current.status == JobStatus.queued
            assert RESTART_WARNING in current.warnings
            current.status = JobStatus.running  # the retry crashed the worker again
            db_session.commit()
        else:
            assert queue.enqueued == [] and current.status == JobStatus.failed
            assert current.error_code == 'worker_crashed' and current.document.status == DocumentStatus.failed
            assert deleted == [str(job.document_id)]


def _stuck_document(session, queued_minutes_ago=0) -> tuple[Document, IngestJob]:
    job = _job(session, JobStatus.queued, 'queued', 0)
    document = session.get(Document, job.document_id)
    document.status = DocumentStatus.pending
    if queued_minutes_ago:
        from datetime import datetime, timedelta, timezone
        job.created_at = datetime.now(timezone.utc) - timedelta(minutes=queued_minutes_ago)
    session.commit()
    return document, job


def test_queue_outage_during_reindex_restores_the_document(db_session, memory_storage):
    document, _ = _stuck_document(db_session)
    document.status = DocumentStatus.failed
    db_session.query(IngestJob).filter(IngestJob.document_id == document.id).update({'status': JobStatus.failed})
    db_session.commit()
    service = DocumentService(db_session, memory_storage, store=None, queue=FailingQueue())
    with pytest.raises(PipelineError):
        service.reindex(document)
    db_session.expire_all()
    assert db_session.get(Document, document.id).status == DocumentStatus.failed
    assert db_session.query(IngestJob).filter(IngestJob.document_id == document.id,
                                              IngestJob.status == JobStatus.queued).count() == 0


def test_stale_jobs_no_longer_block_delete_or_reindex(db_session, memory_storage):
    recent, _ = _stuck_document(db_session)
    stale, stale_job = _stuck_document(db_session, queued_minutes_ago=120)
    queue = RecordingQueue()
    service = DocumentService(db_session, memory_storage, store=None, queue=queue, stale_after_seconds=3600)
    assert service.busy(recent) and not service.busy(stale)
    with pytest.raises(PipelineError) as error:
        service.reindex(recent)
    assert error.value.status_code == 409
    job = service.reindex(stale)
    db_session.expire_all()
    assert queue.enqueued == [job.id]
    assert db_session.get(IngestJob, stale_job.id).status == JobStatus.failed  # the abandoned job never runs


def test_reindex_invalidates_the_cached_summary(db_session):
    job = _job(db_session, JobStatus.succeeded, 'done', 100)
    db_session.add(DocumentSummary(document_id=job.document_id, model='m', prompt_version=1, content='cũ'))
    db_session.commit()
    DocumentRepository(db_session).replace_chunks(job.document_id, [
        Chunk(document_id=job.document_id, ordinal=0, page=1, content='mới', char_start=0, char_end=3)])
    db_session.commit()
    assert db_session.get(DocumentSummary, job.document_id) is None


def test_legacy_wait_stops_when_the_client_disconnects(db_session, memory_storage):
    _, job = _stuck_document(db_session)
    service = DocumentService(db_session, memory_storage, store=None, queue=RecordingQueue())

    async def gone() -> bool:
        return True
    with pytest.raises(PipelineError) as error:
        asyncio.run(service.wait(job.id, timeout_seconds=60, disconnected=gone, poll_seconds=0.01))
    assert error.value.code == 'client_disconnected'
    with pytest.raises(PipelineError) as error:
        asyncio.run(service.wait(job.id, timeout_seconds=0, poll_seconds=0.01))
    assert error.value.code == 'ingest_timeout'


def test_uploaded_filenames_are_stored_in_nfc():
    class Upload:
        filename = unicodedata.normalize('NFD', 'Quy chế nội bộ.pdf')
        file = io.BytesIO(b'%PDF-1.4 not really a pdf')
    name, _ = read_upload(Upload(), Settings(_env_file=None))
    assert name == unicodedata.normalize('NFC', 'Quy chế nội bộ.pdf') and name != Upload.filename
