from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait

import fitz
from PIL import Image

from app.core.errors import PipelineError
from app.models.schemas import DocumentUnit
from app.services.layout import Word, build_text
from app.services.ocr_service import OCRService

OCR_DPI = 200


def _unit(filename: str, number: int, source: str, words: list[Word]) -> DocumentUnit:
    text, placed = build_text(words)
    return DocumentUnit(content=text, layout=placed or None, metadata={
        'filename': filename, 'file_type': 'pdf', 'page': number, 'source_type': source})


def _ocr_page(ocr: OCRService, filename: str, number: int, image: Image.Image) -> DocumentUnit:
    scale = 72 / OCR_DPI  # pixels at OCR_DPI -> PDF points
    try:
        words = [Word(w.text, w.x0 * scale, w.y0 * scale, w.x1 * scale, w.y1 * scale, w.block, w.line)
                 for w in ocr.extract_words(image)]
    finally:
        image.close()
    return _unit(filename, number, 'ocr', words)


def page_count(data: bytes) -> int | None:
    """Pages of a PDF, or None when it cannot be opened (ingestion then reports the real error)."""
    try:
        with fitz.open(stream=data, filetype='pdf') as pdf:
            return pdf.page_count
    except Exception:
        return None


def load_pdf(data: bytes, filename: str, ocr: OCRService, min_text_chars: int = 20,
             progress: Callable[[float], None] | None = None, warn: Callable[[dict], None] | None = None,
             workers: int = 1) -> list[DocumentUnit]:
    """One unit per page: the text layer when it has enough characters, otherwise OCR.

    OCR runs on up to `workers` pages at once (engines that support it). An OCR failure on one page
    becomes a warning and that page falls back to its (short) text layer instead of failing the file.
    """
    report = progress or (lambda fraction: None)
    limit = max(1, workers) if ocr.parallel else 1
    # Opening from bytes also avoids a native Windows file handle remaining
    # locked when MuPDF raises while opening a corrupt PDF.
    with fitz.open(stream=data, filetype='pdf') as document:
        if document.needs_pass:
            raise ValueError('Password-protected PDF is not supported')
        total = document.page_count
        units: list[DocumentUnit | None] = [None] * total
        fallbacks: dict[int, DocumentUnit] = {}
        pending: dict[int, Future] = {}
        finished = 0

        def collect(finished_only: bool) -> None:
            nonlocal finished
            for number, future in list(pending.items()):
                if finished_only and not future.done():
                    continue
                del pending[number]
                try:
                    units[number - 1] = future.result()
                except PipelineError as exc:
                    if warn is not None:
                        warn({'page': number, 'step': 'ocr', 'error': exc.code})
                    units[number - 1] = fallbacks[number]
                finished += 1
                report(finished / total)

        with ThreadPoolExecutor(max_workers=limit) as pool:
            for number, page in enumerate(document, 1):
                words = [Word(w[4], w[0], w[1], w[2], w[3], w[5], w[6]) for w in page.get_text('words', sort=True)]
                unit = _unit(filename, number, 'text', words)
                if sum(char.isalnum() for char in unit.content) >= min_text_chars:
                    units[number - 1] = unit
                    finished += 1
                    report(finished / total)
                    continue
                fallbacks[number] = unit
                # Render in this thread (MuPDF is not thread-safe); keep at most `limit` images in flight.
                while len(pending) >= limit:
                    wait(pending.values(), return_when=FIRST_COMPLETED)
                    collect(finished_only=True)
                pix = page.get_pixmap(dpi=OCR_DPI, colorspace=fitz.csRGB, alpha=False)
                image = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
                pending[number] = pool.submit(_ocr_page, ocr, filename, number, image)
            collect(finished_only=False)
    return [unit for unit in units if unit is not None]
