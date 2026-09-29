"""Answer generation with a local Ollama model. Only the question and retrieved text are sent."""
import logging
import re
import unicodedata

from pydantic import BaseModel

from app.models.schemas import SearchHit
from app.services.ollama_client import OllamaClient
from app.services.prompts import (INSUFFICIENT_ANSWER, NO_CONTEXT_ANSWER, SYSTEM_PROMPT,
                                  build_user_message)

logger = logging.getLogger(__name__)
# Small local models cite in many shapes: [Nguồn: a.pdf, trang 2], (Nguồn: a.pdf trang 1-2),
# [Nguồn: a.pdf, trang 1, 2; b.docx], or the context number itself: [1].
SOURCE_MARKER = re.compile(r'nguồn\s*:', re.IGNORECASE)
PAGE_LIST = re.compile(r'(?:trang|page)\s*:?\s*(\d+(?:\s*(?:[-–,&]|và|and)\s*\d+)*)', re.IGNORECASE)
PAGE_RANGE = re.compile(r'(\d+)(?:\s*[-–]\s*(\d+))?')
NUMBER_MARKER = re.compile(r'\[(\d{1,3})\]')
# Closing bracket that ends a citation, not one inside a filename such as 'Báo cáo [2024].pdf'.
CITATION_END = re.compile(r'[\])](?=\s|$|[.,;:!?](?:\s|$))')
CITATION_BLOCK = re.compile(r'[\[(]?\s*nguồn\s*:.*?(?:[\])](?=\s|$|[.,;:!?](?:\s|$))|$)',
                            re.IGNORECASE | re.MULTILINE)


class GeneratedAnswer(BaseModel):
    supported: bool
    answer: str
    # Zero-based positions of the cited contexts, derived from the model's source markers.
    context_indexes: list[int] = []


def _normalize(text: str) -> str:
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFC', text)).strip().casefold()


def _mentions(text: str, name: str) -> bool:
    return bool(name) and re.search(r'(?<!\w)' + re.escape(name) + r'(?!\w)', text) is not None


def _file_names(hit: SearchHit) -> set[str]:
    # Models sometimes drop the extension: accept 'report' for 'report.pdf'.
    name = _normalize(str(hit.metadata.get('filename', '')))
    return {name, name.rsplit('.', 1)[0]} - {''}


def _pages(segment: str) -> set[int]:
    pages = set()
    for group in PAGE_LIST.findall(segment):
        for low, high in PAGE_RANGE.findall(group):
            start, end = int(low), int(high or low)
            if 0 <= end - start <= 500:
                pages.update(range(start, end + 1))
    return pages


def _source_segments(answer: str) -> list[str]:
    """Text after each 'Nguồn:' up to its closing bracket, the next marker or the line end."""
    starts = [match.end() for match in SOURCE_MARKER.finditer(answer)]
    segments = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(answer)
        segment = answer[start:end].split('\n', 1)[0][:300]
        if closing := CITATION_END.search(segment):
            segment = segment[:closing.start()]
        segments.append(_normalize(segment))
    return segments


def has_citation(answer: str) -> bool:
    return bool(SOURCE_MARKER.search(answer) or NUMBER_MARKER.search(answer))


def _words(text: str) -> str:
    return re.sub(r'[\W_]+', ' ', _normalize(text)).strip()


def is_insufficient(answer: str) -> bool:
    """The refusal sentence alone (citations/punctuation ignored), or inside an uncited reply.

    A cited reply that also refuses one part of the question is a partial answer and is kept.
    """
    target = _words(INSUFFICIENT_ANSWER)
    core = _words(CITATION_BLOCK.sub(' ', NUMBER_MARKER.sub(' ', answer)))
    return core == target or (not has_citation(answer) and target in core)


def cited_indexes(answer: str, contexts: list[SearchHit]) -> list[int]:
    """Map the model's source markers back to retrieved contexts (filename, then page/sheet)."""
    indexes = []
    for segment in _source_segments(answer):
        pages = _pages(segment)
        for index, hit in enumerate(contexts):
            if not any(_mentions(segment, name) for name in _file_names(hit)):
                continue
            page, sheet = hit.metadata.get('page'), hit.metadata.get('sheet')
            if pages and page is not None and page not in pages:
                continue
            if sheet is not None and 'sheet' in segment and not _mentions(segment, _normalize(str(sheet))):
                continue
            indexes.append(index)
    for match in NUMBER_MARKER.finditer(answer):
        if 1 <= int(match.group(1)) <= len(contexts):
            indexes.append(int(match.group(1)) - 1)
    if not indexes:
        # No usable marker: fall back to contexts whose file the answer names.
        text = _normalize(answer)
        indexes = [index for index, hit in enumerate(contexts)
                   if any(_mentions(text, name) for name in _file_names(hit))]
    return list(dict.fromkeys(indexes))


class GenerationService:
    def __init__(self, settings, client: OllamaClient | None = None):
        self.settings = settings
        self.client = client or OllamaClient(settings)

    def answer(self, question: str, contexts: list[SearchHit]) -> GeneratedAnswer:
        """Ollama decides whether the contexts support an answer. Service failures raise PipelineError."""
        if not contexts:
            logger.info('llm skipped: no context')
            return GeneratedAnswer(supported=False, answer=NO_CONTEXT_ANSWER)
        logger.info('ollama called model=%s contexts=%d', self.client.model, len(contexts))
        text = self.client.chat(SYSTEM_PROMPT, build_user_message(question, contexts))
        if is_insufficient(text):
            logger.info('generation success: insufficient context')
            return GeneratedAnswer(supported=False, answer=INSUFFICIENT_ANSWER)
        indexes = cited_indexes(text, contexts)
        logger.info('generation success: cited_contexts=%s', indexes)
        return GeneratedAnswer(supported=True, answer=text, context_indexes=indexes)
