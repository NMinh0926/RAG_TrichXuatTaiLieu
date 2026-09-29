from threading import Lock

import numpy as np
from PIL import Image, ImageOps

from app.core.errors import PipelineError


class OCRService:
    """One lazily initialized PaddleOCR 3.x engine shared by all loaders."""

    def __init__(self, lang: str = 'vi'):
        self.lang = lang
        self._engine = None
        self._lock = Lock()

    def extract(self, image: Image.Image) -> str:
        try:
            with self._lock:
                if self._engine is None:
                    from paddleocr import PaddleOCR
                    self._engine = PaddleOCR(
                        lang=self.lang, ocr_version='PP-OCRv5', device='cpu',
                        use_doc_orientation_classify=False,
                        use_doc_unwarping=False, use_textline_orientation=False,
                    )
                rgb = np.asarray(ImageOps.exif_transpose(image).convert('RGB'))
                # Paddle's ndarray input follows OpenCV's BGR convention.
                results = self._engine.predict(rgb[:, :, ::-1].copy())
                return '\n'.join(text for result in results for text in result['rec_texts'])
        except Exception as exc:
            raise PipelineError('ocr_failure', 'OCR failed; check PaddleOCR models and server logs.', 503) from exc
