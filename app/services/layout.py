"""Page text built from positioned words, so chunk character ranges map back to page rectangles.

Coordinates are PDF points (1/72 inch, origin top-left, as PyMuPDF uses); OCR pixel boxes are scaled
by 72/dpi. A chunk's bounding boxes are its words merged into one rectangle per line.
"""
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class Word:
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    # Words sharing a line key are joined by a space; a new block starts a new paragraph.
    block: int
    line: int


@dataclass(frozen=True)
class Placed:
    start: int
    end: int
    x0: float
    y0: float
    x1: float
    y1: float


def build_text(words: list[Word]) -> tuple[str, list[Placed]]:
    """Join words (in reading order) into text; return it with each word's character span and box."""
    parts: list[str] = []
    placed: list[Placed] = []
    position, previous = 0, None
    for word in words:
        # Any whitespace inside a word (thin or narrow no-break spaces do not split MuPDF words) becomes one
        # space, as clean_text would make it, so cleaning never shifts the character spans.
        text = ' '.join(unicodedata.normalize('NFC', word.text).split())
        if not text:
            continue
        if previous is not None:
            separator = ' ' if (word.block, word.line) == previous else '\n' if word.block == previous[0] else '\n\n'
            parts.append(separator)
            position += len(separator)
        placed.append(Placed(position, position + len(text), word.x0, word.y0, word.x1, word.y1))
        parts.append(text)
        position += len(text)
        previous = (word.block, word.line)
    return ''.join(parts), placed


def chunk_boxes(placed: list[Placed], start: int, end: int, page: int) -> list[dict]:
    """Rectangles covering the words overlapping [start, end), one per visual line, rounded to 0.1 pt."""
    boxes: list[list[float]] = []
    for word in placed:
        if word.end <= start or word.start >= end:
            continue
        if boxes:
            last = boxes[-1]
            overlap = min(last[3], word.y1) - max(last[1], word.y0)
            if overlap > 0.5 * min(last[3] - last[1], word.y1 - word.y0) and word.x0 >= last[0] - 1:
                last[0], last[1] = min(last[0], word.x0), min(last[1], word.y0)
                last[2], last[3] = max(last[2], word.x1), max(last[3], word.y1)
                continue
        boxes.append([word.x0, word.y0, word.x1, word.y1])
    return [{'page': page, 'x0': round(b[0], 1), 'y0': round(b[1], 1), 'x1': round(b[2], 1), 'y1': round(b[3], 1)}
            for b in boxes]
