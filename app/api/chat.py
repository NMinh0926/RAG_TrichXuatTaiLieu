"""Streaming chat (Server-Sent Events over POST) and conversation history."""
import json
import uuid
from collections.abc import Iterator

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.config import Settings, get_settings
from app.core.dependencies import get_chat_service, get_rate_limiter
from app.core.errors import PipelineError
from app.core.ratelimit import RateLimiter
from app.db.models import User
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
def chat(request: ChatRequest, service: ChatService = Depends(get_chat_service),
         user: User = Depends(get_current_user), settings: Settings = Depends(get_settings),
         limiter: RateLimiter = Depends(get_rate_limiter)):
    """Events: meta (conversation_id, intent, rewritten question), progress (summary stages), token (text delta),
    replace (final text supersedes streamed text), done (answer, sources with chunk_id/document_id/page/excerpt),
    error (error, detail, status). Unknown conversation/document ids fail with HTTP 404 before streaming."""
    limiter.check('chat', str(user.id), settings.rate_limit_chat_per_minute)
    events = service.ask(request.question, request.conversation_id, request.document_ids or None, user)
    return StreamingResponse(sse(events), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


def get_conversation_or_404(conversation_id: uuid.UUID, repository: ConversationRepository, user: User):
    conversation = repository.get(conversation_id, user.id)
    if conversation is None:
        raise PipelineError('conversation_not_found', 'Không tìm thấy hội thoại.', 404)
    return conversation


@conversations_router.get('', response_model=ConversationList)
def list_conversations(page: int = Query(1, ge=1, le=100_000), page_size: int = Query(20, ge=1, le=100),
                       session: Session = Depends(get_db), user: User = Depends(get_current_user)):
    items, total = ConversationRepository(session).list(page, page_size, user.id)
    return ConversationList(items=[ConversationOut.model_validate(item) for item in items], total=total,
                            page=page, page_size=page_size)


@conversations_router.get('/{conversation_id}', response_model=ConversationDetail)
def get_conversation(conversation_id: uuid.UUID, session: Session = Depends(get_db),
                     user: User = Depends(get_current_user)):
    return ConversationDetail.model_validate(get_conversation_or_404(conversation_id, ConversationRepository(session),
                                                                     user))


@conversations_router.delete('/{conversation_id}', status_code=204)
def delete_conversation(conversation_id: uuid.UUID, session: Session = Depends(get_db),
                        user: User = Depends(get_current_user)):
    repository = ConversationRepository(session)
    repository.delete(get_conversation_or_404(conversation_id, repository, user))
    session.commit()
    return Response(status_code=204)
