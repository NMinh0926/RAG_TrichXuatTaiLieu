import json
import logging
import time
import uuid
from collections.abc import Iterator
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import ObjectDeletedError

from app.core.config import Settings, get_settings
from app.core.dependencies import get_document_service
from app.core.errors import PipelineError
from app.db.models import Document, IngestJob, JobStatus
from app.db.session import get_db, get_sessionmaker
from app.models.schemas import DocumentList, DocumentOut, JobOut, SubmissionOut, UploadResponse
from app.services.documents import DocumentService
from app.services.file_router import validate_extension

api_router = APIRouter(prefix='/api/documents', tags=['documents'])
jobs_router = APIRouter(prefix='/api/jobs', tags=['jobs'])
logger = logging.getLogger(__name__)


def read_upload(file: UploadFile, settings: Settings) -> tuple[str, bytes]:
    """Validate name/type and read at most MAX_UPLOAD_MB into memory (never onto local disk)."""
    # Both POSIX and Windows separators are stripped from the display filename.
    filename = (file.filename or '').replace('\\', '/').rsplit('/', 1)[-1][:255]
    if any(ord(char) < 32 for char in filename):
        raise PipelineError('invalid_filename', 'Tên file chứa ký tự không hợp lệ.', 400)
    validate_extension(filename)
    limit = settings.max_upload_mb * 1024 * 1024
    blocks, size = [], 0
    try:
        while block := file.file.read(1024 * 1024):
            size += len(block)
            if size > limit:
                raise PipelineError('file_too_large', 'File exceeds MAX_UPLOAD_MB.', 413)
            blocks.append(block)
    finally:
        file.file.close()
    logger.info('upload received filename=%r bytes=%d', filename, size)
    return filename, b''.join(blocks)


def document_out(document: Document, service: DocumentService) -> DocumentOut:
    job = service.latest_job(document.id)
    return DocumentOut.model_validate(document).model_copy(
        update={'latest_job': JobOut.model_validate(job) if job else None})


def get_document_or_404(document_id: uuid.UUID, service: DocumentService) -> Document:
    document = service.repository.get(document_id)
    if document is None:
        raise PipelineError('document_not_found', 'Không tìm thấy tài liệu.', 404)
    return document


@api_router.post('', response_model=SubmissionOut, status_code=202)
def submit_document(file: UploadFile = File(...), settings: Settings = Depends(get_settings),
                    service: DocumentService = Depends(get_document_service)):
    """Store the file and queue processing; returns immediately. Follow progress via /api/jobs/{id}/events."""
    filename, data = read_upload(file, settings)
    submission = service.submit(filename, data)
    try:
        # The job may already have progressed (fast worker, inline queue): report current state.
        service.session.refresh(submission.document)
        if submission.job is not None:
            service.session.refresh(submission.job)
    except (ObjectDeletedError, InvalidRequestError) as exc:
        raise PipelineError('document_deleted', 'Tài liệu đã bị xóa trong lúc xử lý.', 409) from exc
    body = SubmissionOut(document=document_out(submission.document, service),
                         job=JobOut.model_validate(submission.job) if submission.job else None,
                         duplicate=submission.duplicate)
    return JSONResponse(body.model_dump(mode='json'), status_code=200 if submission.duplicate else 202)


@api_router.post('/upload', response_model=UploadResponse)
def upload_document(file: UploadFile = File(...), settings: Settings = Depends(get_settings),
                    service: DocumentService = Depends(get_document_service)):
    """Synchronous upload kept for older clients: queues the job and waits until it finishes."""
    filename, data = read_upload(file, settings)
    submission = service.submit(filename, data)
    if submission.job is not None and submission.job.status not in (JobStatus.succeeded,):
        service.raise_for_failure(service.wait(submission.job.id, settings.ingest_wait_seconds))
    document = service.repository.get(submission.document.id)
    service.session.refresh(document)
    return UploadResponse(filename=document.filename, chunks_created=document.chunk_count,
                          document_id=document.id, duplicate=submission.duplicate)


@api_router.get('', response_model=DocumentList)
def list_documents(page: int = Query(1, ge=1, le=100_000), page_size: int = Query(20, ge=1, le=100),
                   q: str | None = Query(None, max_length=255, pattern=r'^[^\x00]*$'),
                   service: DocumentService = Depends(get_document_service)):
    items, total = service.repository.list(page, page_size, q.strip() if q and q.strip() else None)
    return DocumentList(items=[document_out(item, service) for item in items], total=total,
                        page=page, page_size=page_size)


@api_router.get('/{document_id}', response_model=DocumentOut)
def get_document(document_id: uuid.UUID, service: DocumentService = Depends(get_document_service)):
    return document_out(get_document_or_404(document_id, service), service)


@api_router.get('/{document_id}/file')
def get_document_file(document_id: uuid.UUID, service: DocumentService = Depends(get_document_service)):
    """Stream the original file for in-browser viewing (PDF.js, images)."""
    document = get_document_or_404(document_id, service)
    stored = service.storage.get(document.object_key)
    disposition = f"inline; filename*=UTF-8''{quote(document.filename)}"
    return StreamingResponse(stored.iter_bytes(), media_type=document.content_type,
                             headers={'Content-Disposition': disposition, 'Content-Length': str(stored.size),
                                      'Cache-Control': 'private, max-age=300'})


@api_router.post('/{document_id}/reindex', response_model=JobOut, status_code=202)
def reindex_document(document_id: uuid.UUID, service: DocumentService = Depends(get_document_service)):
    return JobOut.model_validate(service.reindex(get_document_or_404(document_id, service)))


@api_router.delete('/{document_id}', status_code=204)
def delete_document(document_id: uuid.UUID, service: DocumentService = Depends(get_document_service)):
    service.delete(get_document_or_404(document_id, service))
    return Response(status_code=204)


def get_job_or_404(job_id: uuid.UUID, session: Session) -> IngestJob:
    job = session.get(IngestJob, job_id)
    if job is None:
        raise PipelineError('job_not_found', 'Không tìm thấy tác vụ xử lý.', 404)
    return job


@jobs_router.get('/{job_id}', response_model=JobOut)
def get_job(job_id: uuid.UUID, session: Session = Depends(get_db)):
    return JobOut.model_validate(get_job_or_404(job_id, session))


def job_events(job_id: uuid.UUID, poll_seconds: float, timeout_seconds: float) -> Iterator[str]:
    """`progress` whenever step/percent changes, then `done` (succeeded or failed) and the stream ends."""
    last, deadline = None, time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        with get_sessionmaker()() as session:
            job = session.get(IngestJob, job_id)
            snapshot = JobOut.model_validate(job).model_dump(mode='json') if job else None
        if snapshot is None:
            yield f"event: error\ndata: {json.dumps({'error': 'job_not_found'})}\n\n"
            return
        terminal = snapshot['status'] in (JobStatus.succeeded.value, JobStatus.failed.value)
        if terminal or (snapshot['step'], snapshot['progress']) != last:
            last = (snapshot['step'], snapshot['progress'])
            kind = 'done' if terminal else 'progress'
            yield f'event: {kind}\ndata: {json.dumps(snapshot, ensure_ascii=False)}\n\n'
            if terminal:
                return
        time.sleep(poll_seconds)
    yield f"event: error\ndata: {json.dumps({'error': 'stream_timeout'})}\n\n"


@jobs_router.get('/{job_id}/events', response_class=StreamingResponse,
                 responses={200: {'content': {'text/event-stream': {}}}})
def stream_job(job_id: uuid.UUID, session: Session = Depends(get_db), settings: Settings = Depends(get_settings)):
    get_job_or_404(job_id, session)
    return StreamingResponse(job_events(job_id, 0.5, settings.ingest_job_timeout_seconds),
                             media_type='text/event-stream', headers={'Cache-Control': 'no-cache',
                                                                      'X-Accel-Buffering': 'no'})
