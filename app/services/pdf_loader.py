import math
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait

import fitz
from PIL import Image

from app.core.errors import PipelineError
from app.models.schemas import DocumentUnit
from app.services.layout import Word, build_text
from app.services.ocr_service import OCRService

OCR_DPI = 200
# Pixel budget of one rendered OCR page (about 120 MB as RGB). Larger pages (posters, crafted MediaBoxes) are
# rendered at a lower DPI so the few pages in flight cannot exhaust the worker's memory.
MAX_OCR_PIXELS = 40_000_000
# Below this resolution body text is unreadable: such pages keep their text layer and get a warning.
MIN_OCR_DPI = 50


def _unit(filename: str, number: int, source: str, words: list[Word]) -> DocumentUnit:
    text, placed = build_text(words)
    return DocumentUnit(content=text, layout=placed or None, metadata={
        'filename': filename, 'file_type': 'pdf', 'page': number, 'source_type': source})


def _page_words(page: fitz.Page) -> list[Word]:
    """Text-layer words in displayed page coordinates, in reading order.

    MuPDF reports words in unrotated page space; they are mapped through the page's /Rotate so the boxes match
    what pdf.js renders (and the OCR path, which reads the rotated rendering). Blocks are ordered by their
    bottom edge, then left edge, as get_text(sort=True) orders them, while each block keeps MuPDF's line and
    word order, so side-by-side columns are not interleaved line by line.
    """
    matrix = page.rotation_matrix
    blocks: dict[int, list[Word]] = {}
    for x0, y0, x1, y1, text, block, line, _ in page.get_text('words'):
        rect = fitz.Rect(x0, y0, x1, y1) * matrix
        blocks.setdefault(block, []).append(Word(text, rect.x0, rect.y0, rect.x1, rect.y1, block, line))
    ordered = sorted(blocks.values(), key=lambda words: (max(w.y1 for w in words), min(w.x0 for w in words)))
    return [word for words in ordered for word in words]


def _render_dpi(page: fitz.Page) -> int:
    """OCR_DPI, lowered so the rendered page stays within MAX_OCR_PIXELS."""
    area = page.rect.width * page.rect.height
    return min(OCR_DPI, int(72 * math.sqrt(MAX_OCR_PIXELS / area))) if area > 0 else OCR_DPI


def _ocr_page(ocr: OCRService, filename: str, number: int, image: Image.Image, dpi: int) -> DocumentUnit:
    scale = 72 / dpi  # pixels at `dpi` -> PDF points
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
            raise PipelineError('extraction_failure', 'PDF được đặt mật khẩu; hãy gỡ mật khẩu rồi tải lên lại.')
        total = document.page_count
        units: list[DocumentUnit | None] = [None] * total
        fallbacks: dict[int, DocumentUnit] = {}
        pending: dict[int, Future] = {}
        finished = 0

        def done(number: int, unit: DocumentUnit) -> None:
            nonlocal finished
            units[number - 1] = unit
            finished += 1
            report(finished / total)

        def collect(finished_only: bool) -> None:
            for number, future in list(pending.items()):
                if finished_only and not future.done():
                    continue
                del pending[number]
                try:
                    done(number, future.result())
                except PipelineError as exc:
                    if warn is not None:
                        warn({'page': number, 'step': 'ocr', 'error': exc.code})
                    done(number, fallbacks[number])

        with ThreadPoolExecutor(max_workers=limit) as pool:
            for number, page in enumerate(document, 1):
                unit = _unit(filename, number, 'text', _page_words(page))
                if sum(char.isalnum() for char in unit.content) >= min_text_chars:
                    done(number, unit)
                    continue
                dpi = _render_dpi(page)
                if dpi < MIN_OCR_DPI:
                    if warn is not None:
                        warn({'page': number, 'step': 'ocr', 'error': 'page_too_large'})
                    done(number, unit)
                    continue
                fallbacks[number] = unit
                # Render in this thread (MuPDF is not thread-safe); keep at most `limit` images in flight.
                while len(pending) >= limit:
                    wait(pending.values(), return_when=FIRST_COMPLETED)
                    collect(finished_only=True)
                pix = page.get_pixmap(dpi=dpi, colorspace=fitz.csRGB, alpha=False)
                image = Image.frombytes('RGB', (pix.width, pix.height), pix.samples)
                del pix
                pending[number] = pool.submit(_ocr_page, ocr, filename, number, image, dpi)
            collect(finished_only=False)
    return [unit for unit in units if unit is not None]
