"""Question intent: specific question (RAG) vs whole-document summary (map-reduce).

Rules decide the clear cases; the LLM is asked only when a phrasing is ambiguous.
Vietnamese rules accept each syllable either with its correct diacritics or with none at all ("tom tat",
"tóm tắt", "tom tắt"), so users typing without accents are understood while a differently accented word
("chỉnh" vs "chính") does not match.
"""
import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

from app.core.errors import PipelineError
from app.llm.client import LLMClient
from app.llm.prompts import intent_messages

logger = logging.getLogger(__name__)
Intent = Literal['qa', 'summary']


def fold(text: str) -> str:
    """Accent-free, case-folded form: 'Điểm Chính' -> 'diem chinh' (also used by the chat service)."""
    decomposed = unicodedata.normalize('NFD', text.replace('đ', 'd').replace('Đ', 'D'))
    return ''.join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _phrase(words: str) -> str:
    """Regex for a Vietnamese phrase whose syllables may each be typed with or without diacritics."""
    return r'\s+'.join(f'(?:{w}|{fold(w)})' if fold(w) != w else w for w in words.split())


def _forms(words: str) -> frozenset[str]:
    """Accepted spellings of single syllables: as written or accent-free."""
    return frozenset(form for w in words.split() for form in (w, fold(w)))


SUMMARY = re.compile(
    r'\b(' + '|'.join(_phrase(p) for p in ('tóm tắt', 'tóm lược', 'tổng quan', 'khái quát', 'tổng hợp nội dung'))
    + r'|summari[sz]e|summary|overview|main points|key points)\b', re.IGNORECASE)
# "nội dung/ý/điểm chính" (main content/points), checked against compounds such as "chính sách", "thời điểm".
MAIN_POINTS = re.compile(rf'\b(?:{_phrase("nội dung")}|({_phrase("ý")}|{_phrase("điểm")}))\s+{_phrase("chính")}\b',
                         re.IGNORECASE)
# Syllables that turn "chính" into another word: "chính sách", "chính thức", "chính xác", "chinh sua" (chỉnh sửa)...
# "tả" is accepted only accented: unaccented "ta" (we) follows "ý chính" in "các ý chính ta cần nắm".
COMPOUND_AFTER = _forms('sách thức xác trị phủ quyền đáng đảng hãng khách thống trực sửa kiến diện nghĩa sự') | {'tả'}
# Syllables that turn "ý" / "điểm" into another word: "lưu ý", "đồng ý", "thời điểm", "địa điểm", "ưu điểm"...
# "trọng" and "cố" are accepted only accented: "trong" (in) and "co" (unaccented "có") precede "ý chính" too.
COMPOUND_BEFORE = _forms('lưu chú chủ đồng ngụ tùy tuỳ hàm gợi '
                         'thời địa quan ưu nhược đặc khuyết yếu giao tiêu luận cực chấm') | {'trọng', 'cố'}
# "What is it about?"-style requests may mean a summary or a narrow question.
_WHAT = _phrase('gì')
AMBIGUOUS = re.compile(
    rf'\b({_phrase("nói về")}\s+(?:(?:{_phrase("cái")}|{_phrase("điều")}|{_phrase("những")})\s+)?{_WHAT}|'
    rf'{_phrase("đề cập")}\s+(?:(?:{_phrase("đến")}|{_phrase("tới")})\s+)?(?:{_phrase("những")}\s+)?'
    rf'(?:{_phrase("điều")}\s+)?{_WHAT}|{_phrase("giới thiệu")}|(document|file|this) about)\b', re.IGNORECASE)
PREVIOUS_WORD = re.compile(r'(\w+)\s+$')
NEXT_WORD = re.compile(r'\s+(\w+)')


@dataclass
class IntentResult:
    intent: Intent
    source: Literal['rule', 'llm', 'default']


def _asks_main_points(question: str) -> bool:
    for match in MAIN_POINTS.finditer(question):
        after = NEXT_WORD.match(question, match.end())
        if after and after.group(1).casefold() in COMPOUND_AFTER:
            continue
        before = PREVIOUS_WORD.search(question, 0, match.start())
        if match.group(1) and before and before.group(1).casefold() in COMPOUND_BEFORE:
            continue
        return True
    return False


def _is_summary(question: str) -> bool:
    return bool(SUMMARY.search(question)) or _asks_main_points(question)


def classify(question: str, llm: LLMClient | None = None) -> IntentResult:
    # Decomposed input (macOS, Unikey "Unicode tổ hợp") would never match the precomposed patterns.
    question = unicodedata.normalize('NFC', question)
    if _is_summary(question):
        return IntentResult('summary', 'rule')
    if not AMBIGUOUS.search(question) or llm is None:
        return IntentResult('qa', 'rule' if llm is not None else 'default')
    try:
        reply = llm.chat(intent_messages(question), max_tokens=5).casefold()
    except PipelineError:
        # Classification is an optimisation; a failing LLM surfaces on the answer call instead.
        logger.warning('intent classification failed; defaulting to qa')
        return IntentResult('qa', 'default')
    return IntentResult('summary' if 'summary' in reply else 'qa', 'llm')
