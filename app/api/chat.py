"""Streaming chat (Server-Sent Events over POST) and conversation history."""
import json
import uuid
from collections.abc import Iterator

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.dependencies import get_chat_service
from app.core.errors import PipelineError
from app.db.repositories import ConversationRepository
from app.db.session import get_db
from app.models.schemas import ChatRequest, ConversationDetail, ConversationList, ConversationOut
from app.services.chat import ChatEvent, ChatService

chat_router = APIRouter(prefix='/api/chat', tags=['chat'])
conversations_router = APIRouter(prefix='/api/conversations', tags=['conversations'])


def sse(events: Iterator[ChatEvent]) -> Iterator[str]:
    for event in events:
        yield f'event: {event.kind}\ndata: {json.dumps(event.data, ensure_ascii=False)}\n\n'


@chat_router.post('', response_class=StreamingResponse, responses={200: {'content': {'text/event-stream': {}}}})
def chat(request: ChatRequest, service: ChatService = Depends(get_chat_service)):
    """Events: meta (conversation_id, intent, rewritten question), progress (summary stages), token (text delta),
    replace (final text supersedes streamed text), done (answer, sources with chunk_id/document_id/page/excerpt),
    error (error, detail, status). Unknown conversation/document ids fail with HTTP 404 before streaming."""
    events = service.ask(request.question, request.conversation_id, request.document_ids or None)
    return StreamingResponse(sse(events), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


def get_conversation_or_404(conversation_id: uuid.UUID, repository: ConversationRepository):
    conversation = repository.get(conversation_id)
    if conversation is None:
        raise PipelineError('conversation_not_found', 'Không tìm thấy hội thoại.', 404)
    return conversation


@conversations_router.get('', response_model=ConversationList)
def list_conversations(page: int = Query(1, ge=1, le=100_000), page_size: int = Query(20, ge=1, le=100),
                       session: Session = Depends(get_db)):
    items, total = ConversationRepository(session).list(page, page_size)
    return ConversationList(items=[ConversationOut.model_validate(item) for item in items], total=total,
                            page=page, page_size=page_size)


@conversations_router.get('/{conversation_id}', response_model=ConversationDetail)
def get_conversation(conversation_id: uuid.UUID, session: Session = Depends(get_db)):
    return ConversationDetail.model_validate(get_conversation_or_404(conversation_id, ConversationRepository(session)))


@conversations_router.delete('/{conversation_id}', status_code=204)
def delete_conversation(conversation_id: uuid.UUID, session: Session = Depends(get_db)):
    repository = ConversationRepository(session)
    repository.delete(get_conversation_or_404(conversation_id, repository))
    session.commit()
    return Response(status_code=204)
