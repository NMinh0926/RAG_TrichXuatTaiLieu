from pathlib import Path
from PIL import Image
from app.models.schemas import DocumentUnit
from app.services.ocr_service import OCRService


def load_image(path: Path, ocr: OCRService) -> list[DocumentUnit]:
    with Image.open(path) as image:
        text = ocr.extract(image)
    return [DocumentUnit(content=text, metadata={
        'filename': path.name, 'file_type': 'image', 'source_type': 'ocr',
    })]
