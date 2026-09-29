"""Grounded answer generation over retrieved chunks. Only the question and chunk text reach the LLM."""
import logging
import re
import unicodedata
from collections.abc import Iterator

from pydantic import BaseModel

from app.llm.client import LLMClient
from app.llm.prompts import INSUFFICIENT_ANSWER, NO_CONTEXT_ANSWER, rag_messages
from app.models.schemas import SearchHit

logger = logging.getLogger(__name__)
# [1], [2][3], [1, 2], [1-3], [1–3]
CITATION = re.compile(r'\[(\d{1,3}(?:\s*(?:,|-|–)\s*\d{1,3})*)\]')


class GeneratedAnswer(BaseModel):
    supported: bool
    answer: str
    # Zero-based positions (in the contexts sent to the LLM) of the cited chunks, in citation order.
    context_indexes: list[int] = []


def _words(text: str) -> str:
    return re.sub(r'[\W_]+', ' ', unicodedata.normalize('NFC', text)).strip().casefold()


def cited_numbers(answer: str, count: int) -> list[int]:
    """1-based context numbers cited in the answer, in order of first appearance, within 1..count."""
    numbers = []
    for group in CITATION.findall(answer):
        for part in re.split(r'\s*,\s*', group):
            bounds = [int(value) for value in re.split(r'\s*[-–]\s*', part)]
            low, high = bounds[0], bounds[-1]
            if 0 <= high - low <= count:
                numbers.extend(range(low, high + 1))
    return [n for n in dict.fromkeys(numbers) if 1 <= n <= count]


def is_insufficient(answer: str) -> bool:
    """The refusal sentence alone (citations/punctuation ignored), or inside an uncited reply.

    A cited reply that also refuses one part of the question is a partial answer and is kept.
    """
    target = _words(INSUFFICIENT_ANSWER)
    core = _words(CITATION.sub(' ', answer))
    return core == target or (not CITATION.search(answer) and target in core)


class GenerationService:
    def __init__(self, settings, client: LLMClient | None = None):
        self.settings = settings
        self.client = client or LLMClient(settings)

    def finalize(self, text: str, contexts: list[SearchHit]) -> GeneratedAnswer:
        text = text.strip()
        if is_insufficient(text):
            logger.info('generation: insufficient context')
            return GeneratedAnswer(supported=False, answer=INSUFFICIENT_ANSWER)
        indexes = [n - 1 for n in cited_numbers(text, len(contexts))]
        logger.info('generation: supported cited=%s', indexes)
        return GeneratedAnswer(supported=True, answer=text, context_indexes=indexes)

    def answer(self, question: str, contexts: list[SearchHit]) -> GeneratedAnswer:
        """Non-streaming answer. LLM service failures raise PipelineError (never a no-answer)."""
        if not contexts:
            logger.info('llm skipped: no context')
            return GeneratedAnswer(supported=False, answer=NO_CONTEXT_ANSWER)
        logger.info('llm called model=%s contexts=%d', self.client.model, len(contexts))
        return self.finalize(self.client.chat(rag_messages(question, contexts)), contexts)

    def stream(self, question: str, contexts: list[SearchHit]) -> Iterator[str]:
        """Answer deltas; the caller joins them and calls `finalize`. Requires at least one context."""
        logger.info('llm stream model=%s contexts=%d', self.client.model, len(contexts))
        yield from self.client.stream(rag_messages(question, contexts))
