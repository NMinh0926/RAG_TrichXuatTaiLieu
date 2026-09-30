import io
import zipfile
from collections.abc import Callable
from pathlib import PurePath

from app.core.errors import PipelineError
from app.services.docx_loader import load_docx
from app.services.excel_loader import load_excel
from app.services.image_loader import load_image
from app.services.pdf_loader import load_pdf

SUPPORTED_EXTENSIONS = {'.pdf', '.docx', '.xlsx', '.xls', '.png', '.jpg', '.jpeg'}
CONTENT_TYPES = {
    '.pdf': 'application/pdf',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    '.xls': 'application/vnd.ms-excel',
    '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
}
# DOCX/XLSX are ZIP archives whose parsers inflate every part in memory, so a small, highly compressed file
# ("zip bomb") could exhaust the worker. The declared sizes are checked before parsing; zipfile never inflates
# a member beyond its declared size.
ARCHIVE_EXTENSIONS = {'.docx', '.xlsx'}
MAX_ARCHIVE_BYTES = 200 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 10_000


def extension_of(filename: str) -> str:
    return PurePath(filename).suffix.lower()


def validate_extension(filename: str) -> str:
    extension = extension_of(filename)
    if extension not in SUPPORTED_EXTENSIONS:
        raise PipelineError('unsupported_file', 'Định dạng không được hỗ trợ; chỉ nhận PDF, DOCX, XLS/XLSX, PNG, '
                                                'JPG/JPEG.', 415)
    return extension


def content_type_for(filename: str) -> str:
    return CONTENT_TYPES[validate_extension(filename)]


def check_archive(data: bytes) -> None:
    """Reject DOCX/XLSX whose uncompressed content is too large to parse safely."""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            entries = archive.infolist()
    except zipfile.BadZipFile:
        return  # not an archive at all: the loader reports the extraction failure
    if len(entries) > MAX_ARCHIVE_ENTRIES:
        raise PipelineError('file_too_large', f'File chứa {len(entries)} thành phần; giới hạn là '
                                              f'{MAX_ARCHIVE_ENTRIES}.', 413)
    size = sum(entry.file_size for entry in entries)
    if size > MAX_ARCHIVE_BYTES:
        raise PipelineError('file_too_large', f'Nội dung file sau khi giải nén quá lớn ({size // 2**20} MB); giới '
                                              f'hạn là {MAX_ARCHIVE_BYTES // 2**20} MB.', 413)


def load_file(data: bytes, filename: str, ocr, min_text_chars: int,
              progress: Callable[[float], None] | None = None, warn: Callable[[dict], None] | None = None,
              workers: int = 1):
    extension = validate_extension(filename)
    if extension in ARCHIVE_EXTENSIONS:
        check_archive(data)
    if extension == '.pdf':
        return load_pdf(data, filename, ocr, min_text_chars, progress=progress, warn=warn, workers=workers)
    if extension == '.docx':
        return load_docx(data, filename)
    if extension in {'.xls', '.xlsx'}:
        return load_excel(data, filename)
    return load_image(data, filename, ocr)
