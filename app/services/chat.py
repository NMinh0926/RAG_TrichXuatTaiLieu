"""Conversation turn: rewrite follow-up → intent → RAG answer or map-reduce summary → stored with sources.

`ask` yields ChatEvents (sent as Server-Sent Events): meta, progress, token, done, error.
"""
import logging
import re
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.errors import PipelineError
from app.db.models import Document, DocumentStatus, MessageRole
from app.db.repositories import ConversationRepository, DocumentRepository
from app.llm.client import LLMClient
from app.llm.prompts import NO_CONTEXT_ANSWER, multi_summary_messages, rewrite_messages
from app.models.schemas import SearchHit
from app.services.generation import GenerationService
from app.services.intent import classify
from app.services.retrieval import RetrievalService
from app.services.summarizer import SummaryService

logger = logging.getLogger(__name__)
HISTORY_MESSAGES = 6
MAX_SUMMARY_DOCUMENTS = 3
EXCERPT_CHARS = 300


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


class ChatService:
    def __init__(self, session_factory: Callable[[], Session], retrieval: RetrievalService,
                 generation: GenerationService, llm: LLMClient, settings, summary_llm: LLMClient | None = None):
        self.session_factory, self.retrieval, self.generation = session_factory, retrieval, generation
        self.llm, self.settings, self.summary_llm = llm, settings, summary_llm

    def ask(self, question: str, conversation_id: uuid.UUID | None = None,
            document_ids: list[uuid.UUID] | None = None) -> Iterator[ChatEvent]:
        """Validation errors (unknown conversation/document) raise before the first event."""
        session = self.session_factory()
        try:
            conversations = ConversationRepository(session)
            if conversation_id is None:
                conversation = conversations.create(question[:80])
            elif (conversation := conversations.get(conversation_id)) is None:
                raise PipelineError('conversation_not_found', 'Không tìm thấy hội thoại.', 404)
            scope = self._scope(session, document_ids)
            history = [(m.role.value, m.content) for m in conversations.history(conversation.id, HISTORY_MESSAGES)]
            user_message = conversations.add_message(conversation, MessageRole.user, question)
            session.commit()
        except Exception:
            session.close()
            raise
        return self._turn(session, conversation.id, user_message.id, question, history, scope)

    def _scope(self, session: Session, document_ids: list[uuid.UUID] | None) -> list[Document]:
        if not document_ids:
            return []
        repository = DocumentRepository(session)
        documents = [repository.get(document_id) for document_id in dict.fromkeys(document_ids)]
        if any(document is None for document in documents):
            raise PipelineError('document_not_found', 'Không tìm thấy tài liệu trong phạm vi hỏi.', 404)
        return documents

    def _turn(self, session: Session, conversation_id: uuid.UUID, user_message_id: uuid.UUID, question: str,
              history: list[tuple[str, str]], scope: list[Document]) -> Iterator[ChatEvent]:
        try:
            yield ChatEvent('meta', {'conversation_id': str(conversation_id), 'user_message_id': str(user_message_id)})
            standalone = self._standalone(history, question)
            intent = classify(standalone, self.llm)
            yield ChatEvent('meta', {'intent': intent.intent, 'question': standalone})
            pieces: list[str] = []
            if intent.intent == 'summary':
                sources = yield from self._summary(session, standalone, scope, pieces)
                answer = ''.join(pieces).strip()
            else:
                answer, sources = yield from self._rag(session, standalone, scope, pieces)
            conversations = ConversationRepository(session)
            conversation = conversations.get(conversation_id)
            message = conversations.add_message(conversation, MessageRole.assistant, answer, sources)
            session.commit()
            yield ChatEvent('done', {'message_id': str(message.id), 'answer': answer, 'sources': sources,
                                     'intent': intent.intent})
        except PipelineError as exc:
            session.rollback()
            logger.warning('chat turn failed code=%s', exc.code)
            yield ChatEvent('error', {'error': exc.code, 'detail': str(exc), 'status': exc.status_code})
        except Exception:
            session.rollback()
            logger.exception('chat turn failed unexpectedly')
            yield ChatEvent('error', {'error': 'internal_error', 'detail': 'Lỗi hệ thống; xem log server.', 'status': 500})
        finally:
            session.close()

    def _standalone(self, history: list[tuple[str, str]], question: str) -> str:
        if not history:
            return question
        rewritten = self.llm.chat(rewrite_messages(history, question), max_tokens=200).strip().strip('"')
        logger.info('follow-up rewritten chars=%d', len(rewritten))
        return rewritten or question

    def _rag(self, session: Session, question: str, scope: list[Document], pieces: list[str]):
        contexts = self.retrieval.search_candidates(question, document_ids=[str(d.id) for d in scope] or None)
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
        boxes = chunk_bboxes(session, cited)
        sources = [chunk_source(i + 1, contexts[i], boxes.get(contexts[i].id)) for i in verdict.context_indexes]
        return verdict.answer, sources

    def _summary(self, session: Session, question: str, scope: list[Document], pieces: list[str]):
        documents = self._summary_documents(session, question, scope)
        if not documents:
            pieces.append(NO_CONTEXT_ANSWER)
            yield ChatEvent('token', {'text': NO_CONTEXT_ANSWER})
            return []
        summarizer = SummaryService(session, self.llm, self.settings.llm_context_tokens, self.summary_llm)
        repository = DocumentRepository(session)
        single = len(documents) == 1
        summaries = []
        for document in documents:
            parts = []
            for event in summarizer.summarize(document, repository.chunks(document.id), final_stream=single):
                if event.kind == 'progress':
                    yield ChatEvent('progress', event.data)
                else:
                    parts.append(event.data)
                    if single:
                        pieces.append(event.data)
                        yield ChatEvent('token', {'text': event.data})
            summaries.append((document.filename, ''.join(parts).strip()))
        if not single:
            yield ChatEvent('progress', {'stage': 'combine', 'documents': len(documents)})
            for delta in self.llm.stream(multi_summary_messages(summaries, question)):
                pieces.append(delta)
                yield ChatEvent('token', {'text': delta})
        return [document_source(n, document) for n, document in enumerate(documents, 1)]

    def _summary_documents(self, session: Session, question: str, scope: list[Document]) -> list[Document]:
        """Scope if given; else documents named in the question; else the document of the best-matching chunk."""
        if scope:
            return [d for d in scope if d.status == DocumentStatus.ready][:MAX_SUMMARY_DOCUMENTS]
        repository = DocumentRepository(session)
        ready = [d for d in repository.list(1, 100)[0] if d.status == DocumentStatus.ready]
        folded = question.casefold()
        named = [d for d in ready if d.filename.casefold() in folded
                 or re.sub(r'\.[^.]+$', '', d.filename).casefold() in folded]
        if named:
            return named[:MAX_SUMMARY_DOCUMENTS]
        if len(ready) == 1:
            return ready
        hits = self.retrieval.search_candidates(question)
        best = hits[0].metadata.get('document_id') if hits else None
        return [d for d in ready if str(d.id) == best]
