"""Conversation turn: rewrite follow-up → intent → RAG answer or map-reduce summary → stored with sources.

`ask` yields ChatEvents (sent as Server-Sent Events): meta, progress, token, done, error.
Database work uses short-lived sessions: no pooled connection is held while the LLM generates.
"""
import logging
import re
import unicodedata
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.errors import PipelineError
from app.db.models import Document, DocumentStatus, MessageRole, User
from app.db.repositories import ConversationRepository, DocumentRepository
from app.llm.client import LLMClient
from app.llm.prompts import NO_CONTEXT_ANSWER, multi_summary_messages, rewrite_messages
from app.models.schemas import SearchHit
from app.services.generation import GenerationService
from app.services.intent import IntentResult, classify, fold
from app.services.retrieval import RetrievalService
from app.services.summarizer import CHARS_PER_TOKEN, PROMPT_OVERHEAD_TOKENS, SummaryService

logger = logging.getLogger(__name__)
HISTORY_MESSAGES = 6
MAX_SUMMARY_DOCUMENTS = 3
EXCERPT_CHARS = 300
# A standalone question is short: a longer rewrite means the model copied history or answered.
REWRITE_MAX_TOKENS = 80
REWRITE_MIN_CHARS_LIMIT = 300
REWRITE_LABEL = re.compile(r'^\s*(câu hỏi[^:\n]{0,30}|question)\s*:\s*', re.IGNORECASE)
ANSWER_LIKE = re.compile(r'\[\d+\]|^\s*(#|[-*+]\s|\d+[.)]\s)')
QUOTES = '"“”'
# The multi-document introduction; the per-document summaries are appended verbatim by the service.
MULTI_INTRO_TOKENS = 400
# Filenames (or stems) shorter than this ("A.pdf", "1.docx", "QĐ.pdf") are too ambiguous to match in a question.
MIN_NAME_CHARS = 3
# "Tóm tắt Điều 5", "tom tat chuong II", "summarize section 3": a part of a document, answered by RAG with citations.
SECTION = re.compile(r'\b(dieu|khoan|chuong|muc|phan|tiet|trang|section|chapter|article|part|page)\s+'
                     r'(\d+[a-z]?|[ivxlc]{1,6})(?!\w)')
HEADING = re.compile(r'^(#{1,5})(?=\s)', re.MULTILINE)


@dataclass
class ChatEvent:
    kind: str
    data: dict


def chunk_source(number: int, hit: SearchHit, bboxes: list | None = None) -> dict:
    meta = hit.metadata
    return {'n': number, 'chunk_id': hit.id or None, 'document_id': meta.get('document_id'),
            'filename': meta.get('filename'), 'page': meta.get('page'), 'sheet': meta.get('sheet'),
            'excerpt': hit.content[:EXCERPT_CHARS], 'bboxes': bboxes}


def document_source(number: int, document: Document) -> dict:
    return {'n': number, 'chunk_id': None, 'document_id': str(document.id), 'filename': document.filename,
            'page': None, 'sheet': None, 'excerpt': None, 'bboxes': None}


def chunk_bboxes(session: Session, hits: list[SearchHit]) -> dict[str, list | None]:
    """Highlight rectangles of the cited chunks, from PostgreSQL (Chroma metadata cannot hold them)."""
    ids = []
    for hit in hits:
        try:
            ids.append(uuid.UUID(hit.id))
        except ValueError:
            continue
    return {str(key): chunk.bboxes for key, chunk in DocumentRepository(session).chunks_by_ids(ids).items()}


def valid_rewrite(rewritten: str, question: str) -> str | None:
    """The rewritten follow-up, or None when it is empty, bloated, multi-line or looks like an answer."""
    text = rewritten.strip().strip(QUOTES).strip()
    text = REWRITE_LABEL.sub('', text).strip().strip(QUOTES).strip()
    if not text or '\n' in text or ANSWER_LIKE.search(text):
        return None
    return text if len(text) <= max(REWRITE_MIN_CHARS_LIMIT, 4 * len(question)) else None


def names_section(question: str) -> bool:
    return SECTION.search(fold(question)) is not None


def mentioned_name(question: str, filename: str) -> int:
    """Length of the longest distinctive form of `filename` (full name or stem) named as a whole token, else 0."""
    folded = unicodedata.normalize('NFC', question).casefold()
    name = unicodedata.normalize('NFC', filename).casefold()
    forms = {name, re.sub(r'\.[^.]+$', '', name).strip()}
    return max((len(form) for form in forms if len(form) >= MIN_NAME_CHARS
                and re.search(rf'(?<!\w){re.escape(form)}(?!\w)', folded)), default=0)


def demote_headings(text: str) -> str:
    """One markdown heading level down, so a summary nests under its document's '##' heading."""
    return HEADING.sub(r'#\1', text)


class ChatService:
    def __init__(self, session_factory: Callable[[], Session], retrieval: RetrievalService,
                 generation: GenerationService, llm: LLMClient, settings, summary_llm: LLMClient | None = None):
        self.session_factory, self.retrieval, self.generation = session_factory, retrieval, generation
        self.llm, self.settings, self.summary_llm = llm, settings, summary_llm

    def ask(self, question: str, conversation_id: uuid.UUID | None = None,
            document_ids: list[uuid.UUID] | None = None, user: User | None = None) -> Iterator[ChatEvent]:
        """Validation errors (unknown conversation/document) raise before the first event.

        With a `user`, only their conversations and the documents they may read (own + shared) are used;
        None (scripts) means no restriction.
        """
        # Decomposed input (macOS, Unikey "Unicode tổ hợp") is stored, rewritten and matched in NFC.
        question = unicodedata.normalize('NFC', question)
        owner_id = user.id if user is not None else None
        with self.session_factory() as session:
            conversations = ConversationRepository(session)
            if conversation_id is None:
                conversation = conversations.create(question[:80], owner_id)
            elif (conversation := conversations.get(conversation_id, owner_id)) is None:
                raise PipelineError('conversation_not_found', 'Không tìm thấy hội thoại.', 404)
            scope = self._scope(session, document_ids, user)
            history = [(m.role.value, m.content) for m in conversations.history(conversation.id, HISTORY_MESSAGES)]
            user_message = conversations.add_message(conversation, MessageRole.user, question)
            session.commit()
        return self._turn(conversation.id, user_message.id, question, history, scope, user)

    def _scope(self, session: Session, document_ids: list[uuid.UUID] | None, user: User | None) -> list[Document]:
        if not document_ids:
            return []
        repository = DocumentRepository(session)
        documents = [repository.get_visible(document_id, user) for document_id in dict.fromkeys(document_ids)]
        if any(document is None for document in documents):
            raise PipelineError('document_not_found', 'Không tìm thấy tài liệu trong phạm vi hỏi.', 404)
        return documents

    def _turn(self, conversation_id: uuid.UUID, user_message_id: uuid.UUID, question: str,
              history: list[tuple[str, str]], scope: list[Document], user: User | None) -> Iterator[ChatEvent]:
        try:
            yield ChatEvent('meta', {'conversation_id': str(conversation_id), 'user_message_id': str(user_message_id)})
            standalone = self._standalone(history, question)
            intent = classify(standalone, self.llm)
            if intent.intent == 'summary' and names_section(standalone):
                # A section ("Điều 5", "chương 2") is not the whole document: RAG cites and highlights its pages.
                logger.info('summary request names a section; answering with retrieval')
                intent = IntentResult('qa', 'rule')
            yield ChatEvent('meta', {'intent': intent.intent, 'question': standalone})
            pieces: list[str] = []
            with self.session_factory() as session:
                allowed = self._allowed(session, scope, user)
            if intent.intent == 'summary':
                sources = yield from self._summary(standalone, scope, allowed, user, pieces)
                answer = ''.join(pieces).strip()
            else:
                answer, sources = yield from self._rag(standalone, allowed, pieces)
            with self.session_factory() as session:
                conversations = ConversationRepository(session)
                conversation = conversations.get(conversation_id)
                message = conversations.add_message(conversation, MessageRole.assistant, answer, sources)
                session.commit()
            yield ChatEvent('done', {'message_id': str(message.id), 'answer': answer, 'sources': sources,
                                     'intent': intent.intent})
        except PipelineError as exc:
            logger.warning('chat turn failed code=%s', exc.code)
            yield ChatEvent('error', {'error': exc.code, 'detail': str(exc), 'status': exc.status_code})
        except Exception:
            logger.exception('chat turn failed unexpectedly')
            yield ChatEvent('error', {'error': 'internal_error', 'detail': 'Lỗi hệ thống; xem log server.', 'status': 500})

    def _standalone(self, history: list[tuple[str, str]], question: str) -> str:
        """The follow-up rewritten as a standalone question; the original question when the rewrite fails."""
        if not history:
            return question
        try:
            reply = self.llm.chat(rewrite_messages(history, question), max_tokens=REWRITE_MAX_TOKENS)
        except PipelineError as exc:
            # The rewrite is an optimisation; a failing LLM surfaces on the answer call instead.
            logger.warning('follow-up rewrite failed code=%s; using the original question', exc.code)
            return question
        rewritten = valid_rewrite(reply, question)
        logger.info('follow-up rewrite path=%s chars=%d', 'rewritten' if rewritten else 'original', len(reply))
        return rewritten or question

    @staticmethod
    def _allowed(session: Session, scope: list[Document], user: User | None) -> list[str] | None:
        """Document ids retrieval may search: the chosen scope, else every ready document the user may read.
        None = unrestricted (no user). An empty list means nothing is searchable."""
        if scope:
            return [str(d.id) for d in scope]
        if user is None:
            return None
        return [str(d.id) for d in DocumentRepository(session).visible_ready(user)]

    def _rag(self, question: str, allowed: list[str] | None, pieces: list[str]):
        contexts = self.retrieval.search_candidates(question, document_ids=allowed) if allowed != [] else []
        if not contexts:
            pieces.append(NO_CONTEXT_ANSWER)
            yield ChatEvent('token', {'text': NO_CONTEXT_ANSWER})
            return NO_CONTEXT_ANSWER, []
        for delta in self.generation.stream(question, contexts):
            pieces.append(delta)
            yield ChatEvent('token', {'text': delta})
        verdict = self.generation.finalize(''.join(pieces), contexts)
        if not verdict.supported:
            # The streamed text was a refusal: replace it with the canonical sentence.
            yield ChatEvent('replace', {'text': verdict.answer})
            return verdict.answer, []
        cited = [contexts[i] for i in verdict.context_indexes]
        with self.session_factory() as session:
            boxes = chunk_bboxes(session, cited)
        sources = [chunk_source(i + 1, contexts[i], boxes.get(contexts[i].id)) for i in verdict.context_indexes]
        return verdict.answer, sources

    def _summary(self, question: str, scope: list[Document], allowed: list[str] | None, user: User | None,
                 pieces: list[str]):
        documents = self._summary_documents(question, scope, allowed, user)
        if not documents:
            pieces.append(NO_CONTEXT_ANSWER)
            yield ChatEvent('token', {'text': NO_CONTEXT_ANSWER})
            return []
        single = len(documents) == 1
        summaries = []
        for document in documents:
            parts = []
            with self.session_factory() as session:
                chunks = DocumentRepository(session).chunks(document.id)
                # The summarizer ends its read transaction before calling the LLM.
                summarizer = SummaryService(session, self.llm, self.settings.llm_context_tokens, self.summary_llm)
                for event in summarizer.summarize(document, chunks, final_stream=single):
                    if event.kind == 'progress':
                        yield ChatEvent('progress', event.data)
                    else:
                        parts.append(event.data)
                        if single:
                            pieces.append(event.data)
                            yield ChatEvent('token', {'text': event.data})
            summaries.append((document.filename, ''.join(parts).strip()))
        if not single:
            yield from self._combine(question, summaries, pieces)
        return [document_source(n, document) for n, document in enumerate(documents, 1)]

    def _combine(self, question: str, summaries: list[tuple[str, str]], pieces: list[str]) -> Iterator[ChatEvent]:
        """A short generated introduction, then every document's summary under its own heading (appended here, not
        copied by the LLM, so none is lost to the output token limit)."""
        yield ChatEvent('progress', {'stage': 'combine', 'documents': len(summaries)})
        # Each summary's share of the prompt, so the prompt plus the introduction fits the context window.
        share = int((self.settings.llm_context_tokens - MULTI_INTRO_TOKENS - PROMPT_OVERHEAD_TOKENS)
                    * CHARS_PER_TOKEN / len(summaries))
        clipped = [(name, text[:share]) for name, text in summaries]
        for delta in self.llm.stream(multi_summary_messages(clipped, question), max_tokens=MULTI_INTRO_TOKENS):
            pieces.append(delta)
            yield ChatEvent('token', {'text': delta})
        for name, text in summaries:
            block = f'\n\n## {name}\n\n{demote_headings(text)}'
            pieces.append(block)
            yield ChatEvent('token', {'text': block})

    def _summary_documents(self, question: str, scope: list[Document], allowed: list[str] | None,
                           user: User | None) -> list[Document]:
        """Scope if given; else documents named in the question (longest name first); else the document of the
        best-matching chunk."""
        if scope:
            return [d for d in scope if d.status == DocumentStatus.ready][:MAX_SUMMARY_DOCUMENTS]
        with self.session_factory() as session:
            ready = DocumentRepository(session).visible_ready(user)
        if not ready:
            return []
        lengths = {d.id: mentioned_name(question, d.filename) for d in ready}
        named = sorted((d for d in ready if lengths[d.id]), key=lambda d: lengths[d.id], reverse=True)
        if named:
            return named[:MAX_SUMMARY_DOCUMENTS]
        if len(ready) == 1:
            return ready
        hits = self.retrieval.search_candidates(question, document_ids=allowed)
        best = hits[0].metadata.get('document_id') if hits else None
        return [d for d in ready if str(d.id) == best]
