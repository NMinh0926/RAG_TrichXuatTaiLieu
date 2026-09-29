"""OCR with the Tesseract CLI (LSTM models, Vietnamese `vie`), returning positioned words.

Chosen over PaddleOCR PP-OCRv5 by docs/benchmarks/stage3_ocr_ab.json: character error rate 0.09% vs 10.7%
on Vietnamese scans (PaddleOCR's latin model drops stacked diacritics) and 2.3 vs 15.8 s per page.
Each call is a subprocess, so several pages can be recognized in parallel threads.
"""
import csv
import io
import os
import subprocess

from PIL import Image, ImageOps

from app.core.errors import PipelineError
from app.services.layout import Word, build_text

# OCR_LANG accepts ISO codes or Tesseract codes, joined with '+': "vi", "vi+en", "vie".
LANGUAGES = {'vi': 'vie', 'en': 'eng'}


class OCRService:
    parallel = True

    def __init__(self, lang: str = 'vi', timeout_seconds: int = 300):
        self.language = '+'.join(LANGUAGES.get(part, part) for part in lang.split('+'))
        self.timeout_seconds = timeout_seconds

    def extract(self, image: Image.Image) -> str:
        return build_text(self.extract_words(image))[0]

    def extract_words(self, image: Image.Image) -> list[Word]:
        """Words with pixel boxes; lines of a paragraph share a block key (block * 1000 + paragraph)."""
        buffer = io.BytesIO()
        ImageOps.exif_transpose(image).convert('RGB').save(buffer, 'PNG')
        try:
            # Pages already run in parallel processes: one OpenMP thread each (set per process, not
            # globally, which would also single-thread torch embeddings).
            done = subprocess.run(['tesseract', 'stdin', 'stdout', '-l', self.language, '--psm', '3', 'tsv'],
                                  input=buffer.getvalue(), capture_output=True, check=True,
                                  timeout=self.timeout_seconds, env={**os.environ, 'OMP_THREAD_LIMIT': '1'})
        except (OSError, subprocess.SubprocessError) as exc:
            raise PipelineError('ocr_failure', 'OCR failed; check the Tesseract installation and server logs.', 503) from exc
        words = []
        for row in csv.DictReader(io.StringIO(done.stdout.decode('utf-8')), delimiter='\t', quoting=csv.QUOTE_NONE):
            text = (row.get('text') or '').strip()
            if row.get('level') != '5' or not text:
                continue
            left, top = int(row['left']), int(row['top'])
            words.append(Word(text, left, top, left + int(row['width']), top + int(row['height']),
                              block=int(row['block_num']) * 1000 + int(row['par_num']), line=int(row['line_num'])))
        return words
