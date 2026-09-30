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

from app.core.auth import get_current_user
from app.core.config import Settings, get_settings
from app.core.dependencies import get_document_service, get_rate_limiter
from app.core.errors import PipelineError
from app.core.ratelimit import RateLimiter
from app.db.models import Document, IngestJob, JobStatus, User, UserRole
from app.db.repositories import DocumentRepository
from app.db.session import get_db, get_sessionmaker
from app.models.schemas import DocumentList, DocumentOut, DocumentUpdate, JobOut, SubmissionOut, UploadResponse
from app.services.documents import DocumentService
from app.services.file_router import extension_of, validate_extension
from app.services.pdf_loader import page_count

api_router = APIRouter(prefix='/api/documents', tags=['documents'])
jobs_router = APIRouter(prefix='/api/jobs', tags=['jobs'])
logger = logging.getLogger(__name__)


def read_upload(file: UploadFile, settings: Settings) -> tuple[str, bytes]:
    """Validate name/type/size/pages and read at most MAX_UPLOAD_MB into memory (never onto local disk)."""
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
                raise PipelineError('file_too_large', f'File vượt quá giới hạn {settings.max_upload_mb} MB '
                                                      '(MAX_UPLOAD_MB).', 413)
            blocks.append(block)
    finally:
        file.file.close()
    data = b''.join(blocks)
    # Ingest time grows with pages (docs/benchmarks/report.md), so PDFs are also limited by page count.
    if extension_of(filename) == '.pdf' and (pages := page_count(data) or 0) > settings.max_pdf_pages:
        raise PipelineError('too_many_pages', f'PDF có {pages} trang; giới hạn là {settings.max_pdf_pages} trang '
                                              '(MAX_PDF_PAGES).', 413)
    logger.info('upload received filename=%r bytes=%d', filename, size)
    return filename, data


def read_limited_upload(file: UploadFile, settings: Settings, user: User, limiter: RateLimiter) -> tuple[str, bytes]:
    limiter.check('upload', str(user.id), settings.rate_limit_upload_per_minute)
    return read_upload(file, settings)


def can_manage(document: Document, user: User) -> bool:
    """Owners manage their documents; admins also manage shared and ownerless (pre-account) ones."""
    return document.owner_id == user.id or (user.role == UserRole.admin
                                            and (document.is_shared or document.owner_id is None))


def document_out(document: Document, service: DocumentService, user: User) -> DocumentOut:
    job = service.latest_job(document.id)
    return DocumentOut.model_validate(document).model_copy(
        update={'latest_job': JobOut.model_validate(job) if job else None, 'can_manage': can_manage(document, user)})


def get_document_or_404(document_id: uuid.UUID, service: DocumentService, user: User,
                        manage: bool = False) -> Document:
    """Documents the user may not read look nonexistent (404); readable but not manageable ones are 403."""
    document = service.repository.get_visible(document_id, user)
    if document is None:
        raise PipelineError('document_not_found', 'Không tìm thấy tài liệu.', 404)
    if manage and not can_manage(document, user):
        raise PipelineError('forbidden', 'Bạn không có quyền thay đổi tài liệu này.', 403)
    return document


@api_router.post('', response_model=SubmissionOut, status_code=202)
def submit_document(file: UploadFile = File(...), settings: Settings = Depends(get_settings),
                    service: DocumentService = Depends(get_document_service),
                    user: User = Depends(get_current_user), limiter: RateLimiter = Depends(get_rate_limiter)):
    """Store the file and queue processing; returns immediately. Follow progress via /api/jobs/{id}/events."""
    filename, data = read_limited_upload(file, settings, user, limiter)
    submission = service.submit(filename, data, user.id)
    try:
        # The job may already have progressed (fast worker, inline queue): report current state.
        service.session.refresh(submission.document)
        if submission.job is not None:
            service.session.refresh(submission.job)
    except (ObjectDeletedError, InvalidRequestError) as exc:
        raise PipelineError('document_deleted', 'Tài liệu đã bị xóa trong lúc xử lý.', 409) from exc
    body = SubmissionOut(document=document_out(submission.document, service, user),
                         job=JobOut.model_validate(submission.job) if submission.job else None,
                         duplicate=submission.duplicate)
    return JSONResponse(body.model_dump(mode='json'), status_code=200 if submission.duplicate else 202)


@api_router.post('/upload', response_model=UploadResponse)
def upload_document(file: UploadFile = File(...), settings: Settings = Depends(get_settings),
                    service: DocumentService = Depends(get_document_service),
                    user: User = Depends(get_current_user), limiter: RateLimiter = Depends(get_rate_limiter)):
    """Synchronous upload kept for older clients: queues the job and waits until it finishes."""
    filename, data = read_limited_upload(file, settings, user, limiter)
    submission = service.submit(filename, data, user.id)
    if submission.job is not None and submission.job.status not in (JobStatus.succeeded,):
        service.raise_for_failure(service.wait(submission.job.id, settings.ingest_wait_seconds))
    document = service.repository.get(submission.document.id)
    service.session.refresh(document)
    return UploadResponse(filename=document.filename, chunks_created=document.chunk_count,
                          document_id=document.id, duplicate=submission.duplicate)


@api_router.get('', response_model=DocumentList)
def list_documents(page: int = Query(1, ge=1, le=100_000), page_size: int = Query(20, ge=1, le=100),
                   q: str | None = Query(None, max_length=255, pattern=r'^[^\x00]*$'),
                   service: DocumentService = Depends(get_document_service), user: User = Depends(get_current_user)):
    items, total = service.repository.list(page, page_size, q.strip() if q and q.strip() else None, user)
    return DocumentList(items=[document_out(item, service, user) for item in items], total=total,
                        page=page, page_size=page_size)


@api_router.get('/{document_id}', response_model=DocumentOut)
def get_document(document_id: uuid.UUID, service: DocumentService = Depends(get_document_service),
                 user: User = Depends(get_current_user)):
    return document_out(get_document_or_404(document_id, service, user), service, user)


@api_router.patch('/{document_id}', response_model=DocumentOut)
def update_document(document_id: uuid.UUID, update: DocumentUpdate,
                    service: DocumentService = Depends(get_document_service), user: User = Depends(get_current_user)):
    """Share with every user or make private again. Pre-account documents have no owner and stay shared."""
    document = get_document_or_404(document_id, service, user, manage=True)
    if document.owner_id is None and not update.is_shared:
        raise PipelineError('owner_required', 'Tài liệu chưa có chủ sở hữu nên luôn được chia sẻ.', 409)
    document.is_shared = update.is_shared
    service.session.commit()
    logger.info('document sharing changed document_id=%s shared=%s', document.id, document.is_shared)
    return document_out(document, service, user)


@api_router.get('/{document_id}/file')
def get_document_file(document_id: uuid.UUID, service: DocumentService = Depends(get_document_service),
                      user: User = Depends(get_current_user)):
    """Stream the original file for in-browser viewing (PDF.js, images)."""
    document = get_document_or_404(document_id, service, user)
    stored = service.storage.get(document.object_key)
    disposition = f"inline; filename*=UTF-8''{quote(document.filename)}"
    return StreamingResponse(stored.iter_bytes(), media_type=document.content_type,
                             headers={'Content-Disposition': disposition, 'Content-Length': str(stored.size),
                                      # Never kept in the browser cache: access can be revoked (unshare, logout).
                                      'Cache-Control': 'no-store'})


@api_router.post('/{document_id}/reindex', response_model=JobOut, status_code=202)
def reindex_document(document_id: uuid.UUID, service: DocumentService = Depends(get_document_service),
                     user: User = Depends(get_current_user)):
    return JobOut.model_validate(service.reindex(get_document_or_404(document_id, service, user, manage=True)))


@api_router.delete('/{document_id}', status_code=204)
def delete_document(document_id: uuid.UUID, service: DocumentService = Depends(get_document_service),
                    user: User = Depends(get_current_user)):
    service.delete(get_document_or_404(document_id, service, user, manage=True))
    return Response(status_code=204)


def get_job_or_404(job_id: uuid.UUID, session: Session, user: User) -> IngestJob:
    job = session.get(IngestJob, job_id)
    if job is None or DocumentRepository(session).get_visible(job.document_id, user) is None:
        raise PipelineError('job_not_found', 'Không tìm thấy tác vụ xử lý.', 404)
    return job


@jobs_router.get('/{job_id}', response_model=JobOut)
def get_job(job_id: uuid.UUID, session: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return JobOut.model_validate(get_job_or_404(job_id, session, user))


def job_events(job_id: uuid.UUID, user_id: uuid.UUID, token_version: int, poll_seconds: float,
               timeout_seconds: float) -> Iterator[str]:
    """`progress` whenever step/percent changes, then `done` (succeeded or failed) and the stream ends.

    Access is re-checked on every poll: the stream stops when the session is revoked (logout elsewhere,
    password change, deactivation) or the document stops being visible (unshared, deleted).
    """
    last, deadline = None, time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        with get_sessionmaker()() as session:
            user = session.get(User, user_id)
            if user is None or not user.is_active or user.token_version != token_version:
                yield f"event: error\ndata: {json.dumps({'error': 'unauthorized'})}\n\n"
                return
            job = session.get(IngestJob, job_id)
            if job is not None and DocumentRepository(session).get_visible(job.document_id, user) is None:
                job = None
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
def stream_job(job_id: uuid.UUID, session: Session = Depends(get_db), settings: Settings = Depends(get_settings),
               user: User = Depends(get_current_user)):
    get_job_or_404(job_id, session, user)
    events = job_events(job_id, user.id, user.token_version, 0.5, settings.ingest_job_timeout_seconds)
    return StreamingResponse(events, media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
