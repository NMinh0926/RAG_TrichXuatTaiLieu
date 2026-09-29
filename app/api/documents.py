import logging
import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile
from fastapi.responses import StreamingResponse

from app.core.config import Settings, get_settings
from app.core.dependencies import get_document_service
from app.core.errors import PipelineError
from app.models.schemas import DocumentList, DocumentOut, UploadResponse
from app.services.documents import DocumentService
from app.services.file_router import validate_extension

api_router = APIRouter(prefix='/api/documents', tags=['documents'])
logger = logging.getLogger(__name__)


def read_upload(file: UploadFile, settings: Settings) -> tuple[str, bytes]:
    """Validate name/type and read at most MAX_UPLOAD_MB into memory (never onto local disk)."""
    # Both POSIX and Windows separators are stripped from the display filename.
    filename = (file.filename or '').replace('\\', '/').rsplit('/', 1)[-1][:255]
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


def get_document_or_404(document_id: uuid.UUID, service: DocumentService):
    document = service.repository.get(document_id)
    if document is None:
        raise PipelineError('document_not_found', 'Không tìm thấy tài liệu.', 404)
    return document


@api_router.post('/upload', response_model=UploadResponse)
def upload_document(file: UploadFile = File(...), settings: Settings = Depends(get_settings),
                    service: DocumentService = Depends(get_document_service)):
    filename, data = read_upload(file, settings)
    document, duplicate = service.upload(filename, data)
    return UploadResponse(filename=document.filename, chunks_created=document.chunk_count,
                          document_id=document.id, duplicate=duplicate)


@api_router.get('', response_model=DocumentList)
def list_documents(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
                   q: str | None = Query(None, max_length=255),
                   service: DocumentService = Depends(get_document_service)):
    items, total = service.repository.list(page, page_size, q.strip() if q and q.strip() else None)
    return DocumentList(items=[DocumentOut.model_validate(item) for item in items], total=total,
                        page=page, page_size=page_size)


@api_router.get('/{document_id}', response_model=DocumentOut)
def get_document(document_id: uuid.UUID, service: DocumentService = Depends(get_document_service)):
    return DocumentOut.model_validate(get_document_or_404(document_id, service))


@api_router.get('/{document_id}/file')
def get_document_file(document_id: uuid.UUID, service: DocumentService = Depends(get_document_service)):
    """Stream the original file for in-browser viewing (PDF.js, images)."""
    document = get_document_or_404(document_id, service)
    stored = service.storage.get(document.object_key)
    disposition = f"inline; filename*=UTF-8''{quote(document.filename)}"
    return StreamingResponse(stored.iter_bytes(), media_type=document.content_type,
                             headers={'Content-Disposition': disposition, 'Content-Length': str(stored.size),
                                      'Cache-Control': 'private, max-age=300'})


@api_router.delete('/{document_id}', status_code=204)
def delete_document(document_id: uuid.UUID, service: DocumentService = Depends(get_document_service)):
    service.delete(get_document_or_404(document_id, service))
    return Response(status_code=204)
