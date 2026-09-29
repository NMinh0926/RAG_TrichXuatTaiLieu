from pathlib import Path
from uuid import uuid4
import logging

from fastapi import APIRouter, Depends, UploadFile, File
from app.core.config import Settings, get_settings
from app.core.dependencies import get_services
from app.core.errors import PipelineError
from app.models.schemas import IngestionResponse, UploadResponse
from app.services.file_router import validate_extension

router = APIRouter(prefix='/documents', tags=['documents'])
api_router = APIRouter(prefix='/api/documents', tags=['documents'])
logger = logging.getLogger(__name__)


@router.post('/ingest', response_model=IngestionResponse)
def ingest_document(file: UploadFile = File(...), settings: Settings = Depends(get_settings)):
    # Both POSIX and Windows separators are stripped from the display filename.
    filename = (file.filename or '').replace('\\', '/').rsplit('/', 1)[-1]
    validate_extension(Path(filename))
    path = settings.upload_dir / f'{uuid4().hex}{Path(filename).suffix.lower()}'
    succeeded = False
    try:
        settings.upload_dir.mkdir(parents=True, exist_ok=True)
        logger.info('upload received filename=%r file_type=%s', filename, path.suffix)
        size = 0
        with path.open('xb') as target:
            while block := file.file.read(1024 * 1024):
                size += len(block)
                if size > settings.max_upload_mb * 1024 * 1024:
                    raise PipelineError('file_too_large', 'File exceeds MAX_UPLOAD_MB.', 413)
                target.write(block)
        result = get_services()[0].ingest(path, filename)
        succeeded = True
        return result
    except OSError as exc:
        raise PipelineError('upload_failure', 'Không thể lưu hoặc đọc file upload. Kiểm tra dung lượng và quyền thư mục.', 503) from exc
    finally:
        # A failed request must not retain a partial upload. Successful files stay.
        if not succeeded:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning('failed to remove incomplete upload')
        file.file.close()


@api_router.post('/upload', response_model=UploadResponse)
def upload_document(file: UploadFile = File(...), settings: Settings = Depends(get_settings)):
    result = ingest_document(file, settings)
    return UploadResponse(filename=result.filename, chunks_created=result.chunks)
