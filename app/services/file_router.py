from pathlib import Path
from app.core.errors import PipelineError
from app.services.docx_loader import load_docx
from app.services.excel_loader import load_excel
from app.services.image_loader import load_image
from app.services.pdf_loader import load_pdf

SUPPORTED_EXTENSIONS = {'.pdf', '.docx', '.xlsx', '.xls', '.png', '.jpg', '.jpeg'}


def validate_extension(path: Path):
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise PipelineError('unsupported_file', 'Supported formats: PDF, DOCX, XLS/XLSX, PNG, JPG/JPEG.', 415)


def load_file(path: Path, ocr, min_text_chars: int):
    validate_extension(path)
    extension = path.suffix.lower()
    if extension == '.pdf':
        return load_pdf(path, ocr, min_text_chars)
    if extension == '.docx':
        return load_docx(path)
    if extension in {'.xls', '.xlsx'}:
        return load_excel(path)
    return load_image(path, ocr)
