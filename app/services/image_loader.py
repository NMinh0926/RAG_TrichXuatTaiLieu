import io

from PIL import Image
from app.models.schemas import DocumentUnit
from app.services.ocr_service import OCRService


def load_image(data: bytes, filename: str, ocr: OCRService) -> list[DocumentUnit]:
    with Image.open(io.BytesIO(data)) as image:
        text = ocr.extract(image)
    return [DocumentUnit(content=text, metadata={
        'filename': filename, 'file_type': 'image', 'source_type': 'ocr',
    })]
