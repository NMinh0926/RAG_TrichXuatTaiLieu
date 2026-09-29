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


def extension_of(filename: str) -> str:
    return PurePath(filename).suffix.lower()


def validate_extension(filename: str) -> str:
    extension = extension_of(filename)
    if extension not in SUPPORTED_EXTENSIONS:
        raise PipelineError('unsupported_file', 'Supported formats: PDF, DOCX, XLS/XLSX, PNG, JPG/JPEG.', 415)
    return extension


def content_type_for(filename: str) -> str:
    return CONTENT_TYPES[validate_extension(filename)]


def load_file(data: bytes, filename: str, ocr, min_text_chars: int):
    extension = validate_extension(filename)
    if extension == '.pdf':
        return load_pdf(data, filename, ocr, min_text_chars)
    if extension == '.docx':
        return load_docx(data, filename)
    if extension in {'.xls', '.xlsx'}:
        return load_excel(data, filename)
    return load_image(data, filename, ocr)
