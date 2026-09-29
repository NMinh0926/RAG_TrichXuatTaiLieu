"""Deterministic ~4 MB Vietnamese benchmark PDF: text-layer pages plus image-only (scanned) pages.

Synthetic content only. Known facts are planted on known pages so QA timing uses real questions.
Run: python -m scripts.benchmark.make_test_pdf --output .cache/benchmark/test_4mb.pdf
"""
import argparse
import io
import random
from pathlib import Path

import fitz
import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
TITLE = 'QUY CHẾ HOẠT ĐỘNG NỘI BỘ CÔNG TY TNHH SAO MAI'
CHAPTERS = ['Quy định chung', 'Tuyển dụng và thử việc', 'Thời giờ làm việc', 'Nghỉ phép và nghỉ lễ',
            'Lương, thưởng và phụ cấp', 'Đào tạo và phát triển', 'An toàn lao động', 'Công tác phí',
            'Bảo mật thông tin', 'Khen thưởng và kỷ luật', 'Tài sản và thiết bị', 'Điều khoản thi hành']
# (page index 0-based, sentence) - page 30+ are scanned pages, so one fact needs OCR.
FACTS = {
    3: 'Thời gian thử việc đối với vị trí kỹ sư là 45 ngày làm việc.',
    9: 'Nhân viên chính thức được nghỉ phép năm 16 ngày và được cộng thêm 1 ngày cho mỗi 5 năm thâm niên.',
    17: 'Phụ cấp ăn trưa của mỗi nhân viên là 850.000 đồng mỗi tháng.',
    24: 'Mã quy trình phê duyệt công tác phí là CTP-2026-07.',
    31: 'Kho vật tư Sao Mai mở cửa lúc 6 giờ 30 và đóng cửa lúc 21 giờ.',
}
SENTENCES = [
    'Người lao động có trách nhiệm tuân thủ nội quy và các quy trình được ban hành tại {unit}.',
    'Trưởng bộ phận {unit} chịu trách nhiệm hướng dẫn, kiểm tra và báo cáo định kỳ {n} lần mỗi quý.',
    'Mọi đề xuất thay đổi phải được lập thành văn bản và gửi về phòng hành chính trước {n} ngày.',
    'Hồ sơ liên quan được lưu trữ tối thiểu {n} năm theo quy định của pháp luật hiện hành.',
    'Trường hợp phát sinh vướng mắc, nhân viên liên hệ bộ phận {unit} để được giải đáp kịp thời.',
    'Công ty khuyến khích nhân viên đóng góp ý kiến nhằm cải tiến quy trình làm việc tại {unit}.',
    'Việc đánh giá kết quả thực hiện được tiến hành vào cuối mỗi {period} dựa trên các chỉ tiêu đã giao.',
    'Các khoản chi phí phát sinh phải có chứng từ hợp lệ và được phê duyệt trong vòng {n} ngày làm việc.',
    'Nhân viên mới được phân công người hướng dẫn trong {n} tuần đầu tiên để làm quen công việc.',
    'Quy định này áp dụng cho toàn bộ nhân viên, kể cả nhân viên thời vụ và cộng tác viên của {unit}.',
]
UNITS = ['phòng kỹ thuật', 'phòng kinh doanh', 'phòng nhân sự', 'phòng kế toán', 'kho vật tư', 'ban giám đốc']
PERIODS = ['tháng', 'quý', 'năm']


def page_text(rng: random.Random, index: int, sentences_per_page: int) -> str:
    chapter = CHAPTERS[min(index * len(CHAPTERS) // 36, len(CHAPTERS) - 1)]
    lines = [f'Chương {CHAPTERS.index(chapter) + 1}: {chapter}', f'Điều {index * 3 + 1}.']
    body = [rng.choice(SENTENCES).format(unit=rng.choice(UNITS), n=rng.randint(2, 30), period=rng.choice(PERIODS))
            for _ in range(sentences_per_page)]
    if index in FACTS:
        body.insert(len(body) // 2, FACTS[index])
    lines.append(' '.join(body))
    return '\n'.join(lines)


def scanned_page(text: str, rng: np.random.Generator) -> bytes:
    """Render text at 200 dpi A4 with scanner-like noise; returns JPEG bytes."""
    width, height = 1654, 2339
    image = Image.new('L', (width, height), 245)
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(FONT, 34)
    y = 150
    for paragraph in text.split('\n'):
        line = ''
        for word in paragraph.split():
            candidate = f'{line} {word}'.strip()
            if draw.textlength(candidate, font=font) > width - 300:
                draw.text((150, y), line, font=font, fill=20)
                y, line = y + 52, word
            else:
                line = candidate
        draw.text((150, y), line, font=font, fill=20)
        y += 80
    noisy = np.asarray(image, dtype=np.int16) + rng.normal(0, 5, (height, width)).astype(np.int16)
    buffer = io.BytesIO()
    Image.fromarray(np.clip(noisy, 0, 255).astype(np.uint8)).save(buffer, 'JPEG', quality=80)
    return buffer.getvalue()


def page_texts(text_pages: int = 30, scanned_pages: int = 6, sentences_per_page: int = 22) -> list[str]:
    """Ground-truth text of every page, in order (deterministic)."""
    rng = random.Random(20260929)
    texts = [page_text(rng, index, sentences_per_page if index < text_pages else 12)
             for index in range(text_pages + scanned_pages)]
    texts[0] = f'{TITLE}\n{texts[0]}'
    return texts


def build(output: Path, text_pages: int = 30, scanned_pages: int = 6, sentences_per_page: int = 22) -> dict:
    noise = np.random.default_rng(20260929)
    output.parent.mkdir(parents=True, exist_ok=True)
    with fitz.open() as pdf:
        for index, text in enumerate(page_texts(text_pages, scanned_pages, sentences_per_page)):
            page = pdf.new_page(width=595, height=842)
            if index < text_pages:
                page.insert_textbox(fitz.Rect(56, 56, 539, 786), text, fontsize=11, fontname='dejavu', fontfile=FONT)
            else:
                page.insert_image(page.rect, stream=scanned_page(text, noise))
        pdf.save(output, garbage=3, deflate=True)
    return {'path': str(output), 'bytes': output.stat().st_size, 'pages': text_pages + scanned_pages,
            'text_pages': text_pages, 'scanned_pages': scanned_pages, 'facts': FACTS}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('.cache/benchmark/test_4mb.pdf'))
    parser.add_argument('--text-pages', type=int, default=30)
    parser.add_argument('--scanned-pages', type=int, default=6)
    args = parser.parse_args()
    info = build(args.output, args.text_pages, args.scanned_pages)
    print(f"{info['path']}: {info['bytes'] / 1e6:.2f} MB, {info['pages']} pages "
          f"({info['text_pages']} text, {info['scanned_pages']} scanned)")


if __name__ == '__main__':
    main()
