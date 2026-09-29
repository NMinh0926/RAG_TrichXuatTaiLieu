import pytest
from app.services.pdf_loader import load_pdf
from app.services.docx_loader import load_docx
from app.services.excel_loader import load_excel
from app.services.image_loader import load_image
from app.services.file_router import validate_extension
from app.core.errors import PipelineError
from scripts.create_samples import POLICY


def test_01_pdf_text(samples, fake_ocr):
    units = load_pdf((samples / 'sample.pdf').read_bytes(), 'sample.pdf', fake_ocr)
    assert '12 days' in units[0].content
    assert units[0].metadata['page'] == 1
    assert units[0].metadata['filename'] == 'sample.pdf'
    assert units[0].metadata['source_type'] == 'text'
    assert fake_ocr.calls == 0


def test_scan_routing(samples, fake_ocr):
    units = load_pdf((samples / 'sample_scan.pdf').read_bytes(), 'sample_scan.pdf', fake_ocr)
    assert fake_ocr.calls == 1
    assert units[0].metadata['source_type'] == 'ocr'
    assert units[0].metadata['page'] == 1


def test_03_docx_paragraphs_and_table_order(samples):
    text = load_docx((samples / 'sample.docx').read_bytes(), 'sample.docx')[0].content
    assert POLICY in text
    assert 'Nghỉ phép | 12' in text
    assert text.index(POLICY) < text.index('Nghỉ phép | 12') < text.index('Kết thúc')


def test_04_excel_sheets_and_rows(samples):
    units = load_excel((samples / 'sample.xlsx').read_bytes(), 'sample.xlsx')
    assert [unit.metadata['sheet'] for unit in units] == ['Sales', 'Policy']
    assert 'Product: Laptop | Revenue: 150' in units[0].content
    assert 'Department: HR | Leave: 12' in units[1].content


def test_image_routing(samples, fake_ocr):
    unit = load_image((samples / 'sample.jpg').read_bytes(), 'sample.jpg', fake_ocr)[0]
    assert fake_ocr.calls == 1
    assert unit.metadata['source_type'] == 'ocr'


def test_unsupported(samples):
    with pytest.raises(PipelineError, match='Supported formats'):
        validate_extension('test.exe')


def test_mixed_pdf_pages(samples, tmp_path, fake_ocr):
    import fitz
    path = tmp_path / 'mixed.pdf'
    with fitz.open() as mixed:
        for filename in ['sample.pdf', 'sample_scan.pdf']:
            with fitz.open(samples / filename) as source:
                mixed.insert_pdf(source)
        mixed.save(path)
    units = load_pdf(path.read_bytes(), 'mixed.pdf', fake_ocr)
    assert [unit.metadata['page'] for unit in units] == [1, 2]
    assert [unit.metadata['source_type'] for unit in units] == ['text', 'ocr']
    assert fake_ocr.calls == 1
