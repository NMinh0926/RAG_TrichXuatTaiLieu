"""Whole-document summaries by map-reduce over every chunk, cached per document/model/prompt version."""
import logging
from collections.abc import Iterator
from dataclasses import dataclass

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Chunk, Document, DocumentSummary
from app.llm.client import LLMClient
from app.llm.prompts import map_messages, reduce_messages

logger = logging.getLogger(__name__)
# Bump when map/reduce prompts change so cached summaries are regenerated.
PROMPT_VERSION = 1
# Conservative characters-per-token for Vietnamese/English with Qwen-style tokenizers.
CHARS_PER_TOKEN = 2.5
MAP_OUTPUT_TOKENS = 600
PROMPT_OVERHEAD_TOKENS = 400


@dataclass
class SummaryEvent:
    kind: str  # 'progress' | 'token'
    data: dict | str


def document_text(chunks: list[Chunk]) -> list[str]:
    """Reassemble page/sheet texts from ordered chunks, dropping the token-window overlap."""
    sections: list[tuple[tuple, list[str], int]] = []
    for chunk in sorted(chunks, key=lambda c: c.ordinal):
        key = (chunk.page, chunk.sheet)
        if not sections or sections[-1][0] != key:
            sections.append((key, [chunk.content], chunk.char_end))
            continue
        _, parts, previous_end = sections[-1]
        overlap = previous_end - chunk.char_start
        # Legacy chunks have no offsets (char_start 0 everywhere): keep them whole.
        valid = chunk.char_start > 0 and chunk.char_end - chunk.char_start == len(chunk.content)
        parts.append(chunk.content[overlap:].lstrip() if valid and 0 < overlap < len(chunk.content) else chunk.content)
        sections[-1] = (key, parts, chunk.char_end)
    texts = []
    for (page, sheet), parts, _ in sections:
        label = f'[Trang {page}]' if page is not None else f'[Sheet {sheet}]' if sheet is not None else ''
        texts.append(f'{label}\n{" ".join(parts)}'.strip())
    return texts


def group_by_budget(texts: list[str], budget_chars: int) -> list[str]:
    """Consecutive sections packed into groups of at most budget_chars (long sections are split)."""
    groups, current = [], ''
    for text in texts:
        while len(text) > budget_chars:
            if current:
                groups.append(current)
                current = ''
            cut = text.rfind(' ', 0, budget_chars)
            cut = cut if cut > budget_chars // 2 else budget_chars
            groups.append(text[:cut])
            text = text[cut:].lstrip()
        if current and len(current) + len(text) + 2 > budget_chars:
            groups.append(current)
            current = ''
        current = f'{current}\n\n{text}' if current else text
    if current:
        groups.append(current)
    return groups


class SummaryService:
    def __init__(self, session: Session, llm: LLMClient, context_tokens: int, map_llm: LLMClient | None = None,
                 output_tokens: int | None = None):
        """`map_llm` (e.g. a smaller model) summarizes sections; `llm` writes the final summary.

        The final summary may use `output_tokens` (default LLM_MAX_TOKENS, at most a quarter of the context), so
        its input is sized for that output; map/merge inputs are sized for MAP_OUTPUT_TOKENS.
        """
        self.session, self.llm, self.map_llm = session, llm, map_llm or llm
        self.budget_chars = int((context_tokens - MAP_OUTPUT_TOKENS - PROMPT_OVERHEAD_TOKENS) * CHARS_PER_TOKEN)
        self.output_tokens = min(output_tokens or llm.settings.llm_max_tokens, context_tokens // 4)
        self.final_budget_chars = int((context_tokens - self.output_tokens - PROMPT_OVERHEAD_TOKENS) * CHARS_PER_TOKEN)
        self.model_key = self.llm.model if self.map_llm is self.llm else f'{self.llm.model}+{self.map_llm.model}'

    def cached(self, document: Document) -> str | None:
        row = self.session.get(DocumentSummary, document.id)
        if row is not None and row.model == self.model_key and row.prompt_version == PROMPT_VERSION:
            return row.content
        return None

    def summarize(self, document: Document, chunks: list[Chunk], final_stream: bool = True) -> Iterator[SummaryEvent]:
        """Progress events, then the final summary as token events (streamed or one block), then cached."""
        cached = self.cached(document)
        # End the read transaction: no pooled connection is held while the LLM works or the caller streams.
        self.session.commit()
        if cached is not None:
            logger.info('summary cache hit document_id=%s', document.id)
            yield SummaryEvent('progress', {'stage': 'cached', 'document': document.filename})
            yield SummaryEvent('token', cached)
            return
        groups = group_by_budget(document_text(chunks), self.budget_chars)
        partials = groups
        # A single group that is too long for the final prompt is mapped first as well.
        if len(groups) > 1 or sum(len(g) for g in groups) > self.final_budget_chars:
            partials = []
            for part, text in enumerate(groups, 1):
                yield SummaryEvent('progress', {'stage': 'map', 'done': part - 1, 'total': len(groups),
                                                'document': document.filename})
                partials.append(self.map_llm.chat(map_messages(document.filename, part, len(groups), text),
                                                  max_tokens=MAP_OUTPUT_TOKENS))
            # Merge in batches until the partial summaries fit one final prompt.
            while sum(len(p) for p in partials) > self.final_budget_chars and len(partials) > 1:
                yield SummaryEvent('progress', {'stage': 'merge', 'parts': len(partials), 'document': document.filename})
                batches = group_by_budget(partials, self.budget_chars // 2 or 1)
                partials = [self.map_llm.chat(reduce_messages(document.filename, [batch], final=False),
                                              max_tokens=MAP_OUTPUT_TOKENS) for batch in batches]
        yield SummaryEvent('progress', {'stage': 'reduce', 'document': document.filename})
        messages = reduce_messages(document.filename, partials, final=True)
        pieces = []
        if final_stream:
            for delta in self.llm.stream(messages, max_tokens=self.output_tokens):
                pieces.append(delta)
                yield SummaryEvent('token', delta)
        else:
            pieces.append(self.llm.chat(messages, max_tokens=self.output_tokens))
            yield SummaryEvent('token', pieces[0])
        summary = ''.join(pieces).strip()
        self.store(document, summary)
        logger.info('summary stored document_id=%s groups=%d chars=%d', document.id, len(groups), len(summary))

    def store(self, document: Document, summary: str) -> None:
        """Best-effort cache write. An upsert survives a concurrent first summary of the same document; a document
        deleted meanwhile (foreign key violation) only loses the cache entry, not the answer already streamed."""
        values = {'model': self.model_key, 'prompt_version': PROMPT_VERSION, 'content': summary}
        statement = insert(DocumentSummary).values(document_id=document.id, **values)
        try:
            self.session.execute(statement.on_conflict_do_update(index_elements=[DocumentSummary.document_id],
                                                                 set_={**values, 'created_at': func.now()}))
            self.session.commit()
        except IntegrityError:
            self.session.rollback()
            logger.warning('summary not cached document_id=%s (document deleted meanwhile)', document.id)
