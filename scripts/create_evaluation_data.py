"""Small synthetic evaluation corpus with facts specific to each source."""
import json
from pathlib import Path

import fitz
import pandas as pd
from docx import Document
from PIL import Image, ImageDraw, ImageFont


def create_dataset(root: Path):
    corpus = root / 'documents'
    corpus.mkdir(parents=True, exist_ok=True)
    questions = []

    def case(category, question, keywords, filename=None, **location):
        questions.append({'id': len(questions) + 1, 'category': category, 'question': question,
                          'expected_keywords': keywords,
                          'expected_source': {'filename': filename, **location} if filename else None})

    with fitz.open() as pdf:
        pages = [
            ['Orion business travel policy.', 'The Orion hotel allowance is 850 credits per night.',
             'Orion meal reimbursement is limited to 95 credits per day.',
             'Orion travel receipts must be submitted within 14 days.'],
            ['Orion business travel policy continued.', 'Orion mileage reimbursement is 7 credits per kilometer.',
             'The Orion travel approval code is TR-2026.'],
        ]
        for lines in pages:
            page = pdf.new_page()
            page.insert_text((45, 70), '\n'.join(lines), fontsize=12)
        pdf.save(corpus / 'travel.pdf')
    for question, keywords, page in [
        ('What is the Orion hotel allowance per night?', ['850'], 1),
        ('What is the daily Orion meal reimbursement limit?', ['95'], 1),
        ('Within how many days must Orion travel receipts be submitted?', ['14'], 1),
        ('What is the Orion mileage reimbursement per kilometer?', ['7'], 2),
        ('What is the Orion travel approval code?', ['TR-2026'], 2),
    ]:
        case('pdf', question, keywords, 'travel.pdf', page=page)

    document = Document()
    for text in ['Chính sách nhân sự An Bình.',
                 'Nhân viên chính thức được hưởng 12 ngày nghỉ phép mỗi năm.',
                 'Thời gian thử việc tại An Bình là 60 ngày.',
                 'Nhân viên An Bình được làm việc từ xa tối đa 2 ngày mỗi tuần.',
                 'Ngân sách đào tạo tại An Bình là 3000000 đồng mỗi nhân viên mỗi năm.']:
        document.add_paragraph(text)
    document.save(corpus / 'hr.docx')
    for question, keywords in [
        ('Một năm người lao động có bao nhiêu ngày phép?', ['12']),
        ('Thời gian thử việc tại An Bình kéo dài bao nhiêu ngày?', ['60']),
        ('Nhân viên An Bình được làm từ xa mấy ngày một tuần?', ['2']),
        ('Ngân sách đào tạo mỗi nhân viên An Bình mỗi năm là bao nhiêu?', ['3000000']),
    ]:
        case('docx', question, keywords, 'hr.docx')

    with pd.ExcelWriter(corpus / 'operations.xlsx', engine='openpyxl') as writer:
        pd.DataFrame({'Product': ['Atlas Laptop', 'Nova Phone'], 'Revenue': [150, 200]}).to_excel(
            writer, sheet_name='Sales', index=False)
        pd.DataFrame({'Plan': ['Silver', 'Gold'], 'ResponseHours': [24, 4]}).to_excel(
            writer, sheet_name='Support', index=False)
    for question, keywords, sheet in [
        ('What is the revenue for Atlas Laptop?', ['150'], 'Sales'),
        ('What is the revenue for Nova Phone?', ['200'], 'Sales'),
        ('How many response hours does the Silver support plan allow?', ['24'], 'Support'),
        ('How many response hours does the Gold support plan allow?', ['4'], 'Support'),
    ]:
        case('excel', question, keywords, 'operations.xlsx', sheet=sheet)

    font_path = next((p for p in ['C:/Windows/Fonts/arial.ttf',
                                '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'] if Path(p).is_file()), None)
    font = ImageFont.truetype(font_path, 34) if font_path else ImageFont.load_default(size=34)
    notices = [
        ('notice.png', ['Lyra workshop notice.', 'The Lyra workshop starts at 09:30.',
                        'The Lyra workshop is in room B204.', 'The Lyra workshop capacity is 35 people.',
                        'The Lyra workshop registration deadline is October 10.']),
        ('warehouse.png', ['Vega warehouse instructions.', 'The Vega warehouse opens at 07:00.',
                           'The Vega warehouse closes at 18:00.', 'The Vega safety helmet color is yellow.',
                           'The Vega emergency extension is 456.']),
    ]
    for filename, lines in notices:
        with Image.new('RGB', (1400, 420), 'white') as image:
            draw = ImageDraw.Draw(image)
            for index, line in enumerate(lines):
                draw.text((30, 30 + 70 * index), line, font=font, fill='black')
            image.save(corpus / filename)
    with fitz.open() as scan:
        page = scan.new_page(width=700, height=210)
        page.insert_image(page.rect, filename=str(corpus / 'warehouse.png'))
        scan.save(corpus / 'warehouse_scan.pdf')
    for question, keywords in [
        ('What time does the Lyra workshop start?', ['09:30']),
        ('Which room hosts the Lyra workshop?', ['B204']),
        ('How many people can attend the Lyra workshop?', ['35']),
        ('When is the Lyra workshop registration deadline?', ['October', '10']),
    ]:
        case('image', question, keywords, 'notice.png')
    for question, keywords in [
        ('What time does the Vega warehouse open?', ['07:00']),
        ('What time does the Vega warehouse close?', ['18:00']),
        ('What color safety helmet is required at Vega?', ['yellow']),
        ('What is the Vega emergency extension?', ['456']),
    ]:
        case('scan', question, keywords, 'warehouse_scan.pdf', page=1)
    for question in ['CEO của Microsoft là ai?', 'What is the distance from Earth to Mars?',
                     'What is the Orion hotel Wi-Fi password?', 'Ai là giám đốc nhân sự An Bình?']:
        case('no_answer', question, [])
    (root / 'questions.json').write_text(json.dumps(questions, ensure_ascii=False, indent=2), encoding='utf-8')
    return questions


if __name__ == '__main__':
    create_dataset(Path('evaluation'))
    print('Created 25 synthetic evaluation cases in evaluation/')
