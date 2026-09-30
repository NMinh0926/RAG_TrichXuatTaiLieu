"""Deterministic benchmark corpus: text-layer PDFs and scanned (image-only) PDFs of about 1/5/10/50/100 MB.

Text PDFs: Vietnamese pages (same generator as make_test_pdf) each with one photo-like image, the usual
reason real text PDFs are large. Scanned PDFs: 200 dpi page images with scanner noise.
Run: python -m scripts.benchmark.make_corpus --sizes 1 5 10 50 100 --output .cache/benchmark/corpus
"""
import argparse
import io
import json
import random
from pathlib import Path

import fitz
import numpy as np
from PIL import Image

from scripts.benchmark.make_test_pdf import FONT, page_text, scanned_page


def photo(rng: np.random.Generator, width: int = 640, height: int = 400) -> bytes:
    """Smooth random gradients + noise: compresses like a photo (~60-90 KB JPEG)."""
    x = np.linspace(0, 1, width)[None, :, None]
    y = np.linspace(0, 1, height)[:, None, None]
    base = rng.random(3) * 255 * x + rng.random(3) * 255 * y
    image = np.clip(base / 2 + rng.normal(0, 18, (height, width, 3)), 0, 255).astype(np.uint8)
    buffer = io.BytesIO()
    Image.fromarray(image).save(buffer, 'JPEG', quality=85)
    return buffer.getvalue()


def build(kind: str, megabytes: int, output: Path) -> dict:
    target = megabytes * 1_000_000
    text_rng, noise = random.Random(megabytes), np.random.default_rng(megabytes)
    # The embedded DejaVu font subset adds ~410 KB once per text PDF.
    size, pages = (410_000 if kind == 'text' else 0), 0
    with fitz.open() as pdf:
        while size < target:
            page = pdf.new_page(width=595, height=842)
            text = page_text(text_rng, pages, 18 if kind == 'text' else 12)
            if kind == 'text':
                page.insert_textbox(fitz.Rect(56, 56, 539, 520), text, fontsize=11, fontname='dejavu', fontfile=FONT)
                image = photo(noise)
                page.insert_image(fitz.Rect(96, 540, 499, 792), stream=image)
                size += len(image) + 2500
            else:
                image = scanned_page(text, noise)
                page.insert_image(page.rect, stream=image)
                size += len(image)
            pages += 1
        pdf.save(output, garbage=3, deflate=True)
    return {'file': output.name, 'kind': kind, 'target_mb': megabytes, 'bytes': output.stat().st_size, 'pages': pages}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--sizes', type=int, nargs='+', default=[1, 5, 10, 50, 100])
    parser.add_argument('--kinds', nargs='+', default=['text', 'scan'], choices=['text', 'scan'])
    parser.add_argument('--output', type=Path, default=Path('.cache/benchmark/corpus'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = [build(kind, size, args.output / f'{kind}_{size}mb.pdf') for kind in args.kinds for size in args.sizes]
    (args.output / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    for row in manifest:
        print(f"{row['file']}: {row['bytes'] / 1e6:.1f} MB, {row['pages']} pages")


if __name__ == '__main__':
    main()
