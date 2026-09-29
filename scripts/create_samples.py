"""Create deterministic fixtures; contains no confidential or real employee data."""
from pathlib import Path
import fitz
import pandas as pd
from docx import Document
from PIL import Image, ImageDraw, ImageFont

POLICY = 'Nhân viên chính thức được hưởng 12 ngày nghỉ phép mỗi năm.'
OCR_TEXT = 'Employees receive 12 days of annual leave.'


def create_samples(directory: Path):
    directory.mkdir(parents=True, exist_ok=True)
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((60, 100), OCR_TEXT, fontsize=18)
        pdf.save(directory / 'sample.pdf')
    document = Document()
    document.add_paragraph('Chính sách nhân sự')
    document.add_paragraph(POLICY)
    table = document.add_table(rows=2, cols=2)
    for row, values in zip(table.rows, [('Hạng mục', 'Số ngày'), ('Nghỉ phép', '12')]):
        for cell, value in zip(row.cells, values):
            cell.text = value
    document.add_paragraph('Kết thúc chính sách.')
    document.save(directory / 'sample.docx')
    with pd.ExcelWriter(directory / 'sample.xlsx', engine='openpyxl') as writer:
        pd.DataFrame({'Product': ['Laptop', 'Phone'], 'Revenue': [150, 200]}).to_excel(
            writer, sheet_name='Sales', index=False)
        pd.DataFrame({'Department': ['HR'], 'Leave': [12]}).to_excel(writer, sheet_name='Policy', index=False)
    font = None
    for candidate in ['C:/Windows/Fonts/arial.ttf', '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']:
        if Path(candidate).is_file():
            font = ImageFont.truetype(candidate, 36)
            break
    font = font or ImageFont.load_default(size=36)
    with Image.new('RGB', (1200, 240), 'white') as image:
        ImageDraw.Draw(image).text((35, 80), OCR_TEXT, font=font, fill='black')
        image.save(directory / 'sample.jpg', quality=95)
        image.save(directory / 'sample.png')
        with fitz.open() as scan:
            page = scan.new_page(width=600, height=120)
            page.insert_image(page.rect, filename=str(directory / 'sample.jpg'))
            scan.save(directory / 'sample_scan.pdf')


if __name__ == '__main__':
    create_samples(Path('sample_data'))
    print('Created samples in sample_data/')
