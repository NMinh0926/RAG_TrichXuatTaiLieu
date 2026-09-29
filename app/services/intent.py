"""Question intent: specific question (RAG) vs whole-document summary (map-reduce).

Rules decide the clear cases; the LLM is asked only when a phrasing is ambiguous.
"""
import logging
import re
from dataclasses import dataclass
from typing import Literal

from app.core.errors import PipelineError
from app.llm.client import LLMClient
from app.llm.prompts import intent_messages

logger = logging.getLogger(__name__)
Intent = Literal['qa', 'summary']

SUMMARY = re.compile(
    r'\b(tóm tắt|tóm lược|tổng quan|khái quát|tổng hợp nội dung|(nội dung|ý|điểm) chính|'
    r'summari[sz]e|summary|overview|main points|key points)\b', re.IGNORECASE)
# "What is it about?"-style requests may mean a summary or a narrow question.
AMBIGUOUS = re.compile(
    r'\b(nói về (gì|cái gì|điều gì|những gì)|đề cập (đến |tới )?(những )?(gì|điều gì)|giới thiệu|'
    r'(document|file|this) about)\b', re.IGNORECASE)


@dataclass
class IntentResult:
    intent: Intent
    source: Literal['rule', 'llm', 'default']


def classify(question: str, llm: LLMClient | None = None) -> IntentResult:
    if SUMMARY.search(question):
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
