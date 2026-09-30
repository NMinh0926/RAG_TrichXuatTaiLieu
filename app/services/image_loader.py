import io

from PIL import Image

from app.core.errors import PipelineError
from app.models.schemas import DocumentUnit
from app.services.ocr_service import OCRService

# Pillow only warns below ~179M pixels; the image is decoded in full, then converted and re-encoded for OCR.
MAX_IMAGE_PIXELS = 100_000_000
# Formats matching the accepted extensions (phone JPEGs are often MPO); anything else is not decoded.
FORMATS = ['PNG', 'JPEG', 'MPO']


def load_image(data: bytes, filename: str, ocr: OCRService) -> list[DocumentUnit]:
    too_large = PipelineError('file_too_large', f'Ảnh quá lớn; giới hạn là {MAX_IMAGE_PIXELS // 1_000_000} triệu '
                                                'điểm ảnh.', 413)
    try:
        image = Image.open(io.BytesIO(data), formats=FORMATS)
    except Image.DecompressionBombError as exc:
        raise too_large from exc
    with image:
        # Only the header has been read so far: the pixels are decoded by OCR, after this check.
        if image.width * image.height > MAX_IMAGE_PIXELS:
            raise too_large
        text = ocr.extract(image)
    return [DocumentUnit(content=text, metadata={
        'filename': filename, 'file_type': 'image', 'source_type': 'ocr',
    })]
