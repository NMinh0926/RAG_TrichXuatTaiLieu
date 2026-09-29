import fitz
from PIL import Image

from app.models.schemas import DocumentUnit
from app.services.ocr_service import OCRService


def load_pdf(data: bytes, filename: str, ocr: OCRService, min_text_chars: int = 20) -> list[DocumentUnit]:
    units = []
    # Opening from bytes also avoids a native Windows file handle remaining
    # locked when MuPDF raises while opening a corrupt PDF.
    with fitz.open(stream=data, filetype='pdf') as document:
        if document.needs_pass:
            raise ValueError('Password-protected PDF is not supported')
        for number, page in enumerate(document, 1):
            text = page.get_text(sort=True)
            meaningful = sum(char.isalnum() for char in text)
            source = 'text'
            if meaningful < min_text_chars:
                pix = page.get_pixmap(dpi=200, colorspace=fitz.csRGB, alpha=False)
                with Image.frombytes('RGB', (pix.width, pix.height), pix.samples) as image:
                    recognized = ocr.extract(image)
                # A short real text page can be more useful than an empty OCR result.
                if recognized.strip():
                    text = recognized
                source = 'ocr'
            units.append(DocumentUnit(content=text, metadata={
                'filename': filename, 'file_type': 'pdf', 'page': number, 'source_type': source,
            }))
    return units
