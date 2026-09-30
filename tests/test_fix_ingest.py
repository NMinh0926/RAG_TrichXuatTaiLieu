"""Ingest fixes: rotated PDF boxes, column reading order, whitespace inside words, resource limits, messages."""
import asyncio
import io
import json
import zipfile
from pathlib import Path

import fitz
import pytest
from PIL import Image

from app.core.config import Settings
from app.core.errors import PipelineError
from app.services import file_router, image_loader, pdf_loader
from app.services.ingestion import IngestionService
from app.services.layout import Word, build_text
from app.services.pdf_loader import load_pdf
from app.services.text_cleaner import clean_text
from tests.conftest import FakeEmbedding, FakeOCR

DEJAVU = Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')


class RecordingOCR(FakeOCR):
    def __init__(self):
        super().__init__()
        self.sizes: list[tuple[int, int]] = []

    def extract_words(self, image):
        self.sizes.append(image.size)
        return super().extract_words(image)


class MemoryStore:
    def __init__(self):
        self.chunks = []

    def add(self, document_id, chunks, embeddings, ids):
        self.chunks.extend(chunks)


def _pdf(width: float = 600, height: float = 800, rotation: int = 0, texts: tuple[tuple[float, float, str], ...] = (),
         **insert) -> bytes:
    with fitz.open() as pdf:
        page = pdf.new_page(width=width, height=height)
        for x, y, text in texts:
            page.insert_text((x, y), text, **insert)
        page.set_rotation(rotation)
        return pdf.tobytes()


def _service(settings: Settings | None = None, ocr=None) -> tuple[IngestionService, MemoryStore]:
    store = MemoryStore()
    return IngestionService(settings or Settings(_env_file=None), ocr or FakeOCR(), FakeEmbedding(), store), store


# Finding 7/25: text-layer boxes are in the displayed (rotated) page space, as pdf.js renders it.
@pytest.mark.parametrize('rotation,right,bottom', [(0, False, False), (90, True, False), (180, True, True),
                                                   (270, False, True)])
def test_rotated_page_boxes_follow_display(rotation, right, bottom):
    data = _pdf(rotation=rotation, texts=((50, 100, 'Hello rotated world'),))
    with fitz.open(stream=data, filetype='pdf') as pdf:
        display = pdf[0].rect
    unit = load_pdf(data, 'r.pdf', FakeOCR(), min_text_chars=1)[0]
    assert unit.metadata['source_type'] == 'text' and unit.layout
    for box in unit.layout:
        assert 0 <= box.x0 < box.x1 <= display.width and 0 <= box.y0 < box.y1 <= display.height
        assert (box.x0 > display.width / 2) == right and (box.y0 > display.height / 2) == bottom


def test_rotated_boxes_match_rendered_ink():
    """The box of a word covers dark pixels of the page rendered with its rotation (what the viewer shows)."""
    data = _pdf(rotation=90, texts=((50, 100, 'MMMMMMMM'),), fontsize=30)
    unit = load_pdf(data, 'r.pdf', FakeOCR(), min_text_chars=1)[0]
    box = unit.layout[0]
    with fitz.open(stream=data, filetype='pdf') as pdf:
        pix = pdf[0].get_pixmap(dpi=72, colorspace=fitz.csGRAY, clip=fitz.Rect(box.x0, box.y0, box.x1, box.y1))
    assert min(pix.samples) < 100


# Finding 36: side-by-side columns are not interleaved line by line.
def test_two_columns_keep_their_order():
    with fitz.open() as pdf:
        page = pdf.new_page(width=600, height=400)
        page.insert_textbox(fitz.Rect(40, 40, 280, 200), '\n'.join(f'Cot trai dong {i}' for i in range(1, 4)))
        page.insert_textbox(fitz.Rect(320, 40, 560, 200), '\n'.join(f'Cot phai dong {i}' for i in range(1, 4)))
        data = pdf.tobytes()
    text = load_pdf(data, 'cols.pdf', FakeOCR(), min_text_chars=1)[0].content
    left = [text.index(f'Cot trai dong {i}') for i in range(1, 4)]
    right = [text.index(f'Cot phai dong {i}') for i in range(1, 4)]
    assert left == sorted(left) and right == sorted(right)
    assert max(left) < min(right)


def test_blocks_ordered_top_to_bottom():
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_textbox(fitz.Rect(40, 300, 500, 340), 'Doan hai')
        page.insert_textbox(fitz.Rect(40, 60, 500, 100), 'Doan mot')
        data = pdf.tobytes()
    text = load_pdf(data, 'order.pdf', FakeOCR(), min_text_chars=1)[0].content
    assert text.index('Doan mot') < text.index('Doan hai')


# Finding 37: whitespace inside a word must not make cleaning drop the page's boxes.
def test_whitespace_inside_word_is_normalized():
    words = [Word('định thời', 10, 10, 60, 20, 0, 0), Word('va abc', 70, 10, 100, 20, 0, 0),
             Word('x  y', 10, 30, 40, 40, 0, 1)]
    text, placed = build_text(words)
    assert text == 'định thời va abc\nx y'
    assert clean_text(text) == text
    assert [text[p.start:p.end] for p in placed] == ['định thời', 'va abc', 'x y']


def test_whitespace_inside_word_keeps_layout_through_ingestion(monkeypatch):
    def load(data, filename, *args, **kwargs):
        return [pdf_loader._unit(filename, 1, 'text', [Word('định thời', 10, 10, 60, 20, 0, 0),
                                                      Word('biểu mẫu hợp lệ', 70, 10, 150, 20, 0, 0)])]
    monkeypatch.setattr('app.services.ingestion.load_file', load)
    service, store = _service()
    processed = service.process(b'%PDF', 'w.pdf', 'doc')
    assert processed.units[0].layout is not None
    assert store.chunks[0].bboxes


@pytest.mark.skipif(not DEJAVU.is_file(), reason='DejaVu font not installed')
def test_thin_space_pdf_keeps_boxes():
    data = _pdf(texts=((50, 100, 'dinh thoi va abc x y z w'),), fontfile=str(DEJAVU), fontname='dejavu')
    service, store = _service()
    processed = service.process(data, 'thin.pdf', 'doc')
    assert processed.units[0].layout is not None
    assert store.chunks[0].bboxes


# Finding 11: resource limits.
def test_huge_pdf_page_is_rendered_within_budget(monkeypatch):
    monkeypatch.setattr(pdf_loader, 'MAX_OCR_PIXELS', 1_000_000)
    ocr = RecordingOCR()
    unit = load_pdf(_pdf(width=1000, height=1000), 'big.pdf', ocr)[0]
    assert unit.metadata['source_type'] == 'ocr'
    width, height = ocr.sizes[0]
    assert width * height <= 1_000_000 and width == 1000  # 72 dpi instead of 200
    # FakeOCR's first word spans pixels 0-90: at 72 dpi that is 90 points.
    assert unit.layout[0].x1 == pytest.approx(90)


def test_pdf_page_too_large_to_ocr_is_skipped_with_warning(monkeypatch):
    monkeypatch.setattr(pdf_loader, 'MAX_OCR_PIXELS', 1_000_000)
    warnings: list[dict] = []
    ocr = RecordingOCR()
    units = load_pdf(_pdf(width=3000, height=3000), 'poster.pdf', ocr, warn=warnings.append)
    assert ocr.sizes == [] and len(units) == 1 and units[0].metadata['source_type'] == 'text'
    assert warnings == [{'page': 1, 'step': 'ocr', 'error': 'page_too_large'}]


def test_normal_page_keeps_ocr_dpi():
    ocr = RecordingOCR()
    load_pdf(_pdf(width=595, height=842), 'a4.pdf', ocr)
    assert ocr.sizes == [(round(595 * 200 / 72), round(842 * 200 / 72))]


def _zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return buffer.getvalue()


@pytest.mark.parametrize('filename', ['bomb.docx', 'bomb.xlsx'])
def test_archive_uncompressed_size_limit(monkeypatch, filename):
    monkeypatch.setattr(file_router, 'MAX_ARCHIVE_BYTES', 1024 * 1024)
    data = _zip({'word/document.xml': b'<a>' + b'0' * (2 * 1024 * 1024) + b'</a>'})
    assert len(data) < 100 * 1024
    service, _ = _service()
    with pytest.raises(PipelineError) as error:
        service.process(data, filename, 'doc')
    assert error.value.code == 'file_too_large' and error.value.status_code == 413
    assert 'giải nén' in str(error.value)


def test_archive_entry_count_limit(monkeypatch):
    monkeypatch.setattr(file_router, 'MAX_ARCHIVE_ENTRIES', 5)
    data = _zip({f'part{i}.xml': b'<a/>' for i in range(6)})
    with pytest.raises(PipelineError) as error:
        _service()[0].process(data, 'many.docx', 'doc')
    assert error.value.code == 'file_too_large'


def test_normal_docx_passes_archive_check(samples):
    file_router.check_archive((samples / 'sample.docx').read_bytes())
    file_router.check_archive((samples / 'sample.xlsx').read_bytes())


def test_image_pixel_limit(monkeypatch, samples):
    monkeypatch.setattr(image_loader, 'MAX_IMAGE_PIXELS', 1000)
    ocr = FakeOCR()
    with pytest.raises(PipelineError) as error:
        image_loader.load_image((samples / 'sample.png').read_bytes(), 'sample.png', ocr)
    assert error.value.code == 'file_too_large' and ocr.calls == 0


def test_image_decompression_bomb_is_rejected(monkeypatch, samples):
    monkeypatch.setattr(Image, 'MAX_IMAGE_PIXELS', 1000)
    with pytest.raises(PipelineError) as error:
        image_loader.load_image((samples / 'sample.png').read_bytes(), 'sample.png', FakeOCR())
    assert error.value.code == 'file_too_large'


def test_image_format_must_match_accepted_types():
    buffer = io.BytesIO()
    Image.new('RGB', (40, 40), 'white').save(buffer, 'GIF')
    with pytest.raises(PipelineError) as error:
        _service()[0].process(buffer.getvalue(), 'renamed.png', 'doc')
    assert error.value.code == 'extraction_failure'


def test_long_docx_is_limited_like_pdf_pages(tmp_path):
    from docx import Document
    document = Document()
    document.add_paragraph('chữ ' * 3000)
    path = tmp_path / 'long.docx'
    document.save(path)
    service, _ = _service(Settings(max_pdf_pages=1, _env_file=None))
    with pytest.raises(PipelineError) as error:
        service.process(path.read_bytes(), 'long.docx', 'doc')
    assert error.value.code == 'file_too_large' and 'MAX_PDF_PAGES' in str(error.value)


# Findings 16 and 22: user-facing messages are Vietnamese.
@pytest.mark.parametrize('filename,data,code,text', [
    ('empty.pdf', b'', 'empty_document', 'File trống'),
    ('bad.pdf', b'corrupt PDF', 'extraction_failure', 'Không đọc được file'),
    ('blank.pdf', _pdf(), 'no_usable_text', 'Không tìm thấy nội dung'),
    ('virus.exe', b'MZ', 'unsupported_file', 'Định dạng không được hỗ trợ'),
])
def test_ingest_errors_are_vietnamese(filename, data, code, text):
    with pytest.raises(PipelineError) as error:
        _service(ocr=type('EmptyOCR', (FakeOCR,), {'extract_words': lambda self, image: []})())[0].process(
            data, filename, 'doc')
    assert error.value.code == code and str(error.value).startswith(text)


def test_password_protected_pdf_message():
    with fitz.open() as pdf:
        pdf.new_page().insert_text((50, 100), 'secret')
        data = pdf.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw='owner', user_pw='user')
    with pytest.raises(PipelineError) as error:
        _service()[0].process(data, 'locked.pdf', 'doc')
    assert error.value.code == 'extraction_failure' and 'mật khẩu' in str(error.value)


def test_ocr_failure_message_is_vietnamese(monkeypatch):
    import subprocess
    from app.services.ocr_service import OCRService

    def broken(*args, **kwargs):
        raise subprocess.CalledProcessError(1, 'tesseract')
    monkeypatch.setattr(subprocess, 'run', broken)
    with pytest.raises(PipelineError) as error:
        OCRService().extract_words(Image.new('RGB', (10, 10)))
    assert str(error.value).startswith('Không nhận dạng được chữ')


def test_unexpected_error_detail_is_vietnamese():
    from app.main import unexpected_error_handler
    response = asyncio.run(unexpected_error_handler(None, RuntimeError('boom')))
    body = json.loads(response.body)
    assert response.status_code == 500 and body['error'] == 'internal_error'
    assert body['detail'].startswith('Lỗi máy chủ') and 'boom' not in body['detail']
