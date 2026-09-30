import logging
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.config import Settings, get_settings
from app.core.dependencies import get_generation, get_rate_limiter, get_services
from app.core.errors import PipelineError
from app.core.ratelimit import RateLimiter
from app.db.models import User
from app.db.repositories import DocumentRepository
from app.db.session import get_db
from app.models.schemas import QuestionRequest, QuestionResponse, AnswerResponse
from app.services.answering import answer_from_contexts

router = APIRouter(prefix='/questions', tags=['questions'])
api_router = APIRouter(prefix='/api/questions', tags=['questions'])
logger = logging.getLogger(__name__)


def get_readable_document_ids(session: Session = Depends(get_db),
                              user: User = Depends(get_current_user)) -> list[str] | None:
    """Ready documents the user may read (own + shared); retrieval never searches outside them."""
    return [str(document.id) for document in DocumentRepository(session).visible_ready(user)]


@router.post('/search', response_model=QuestionResponse)
def search_question(request: QuestionRequest, readable: list[str] | None = Depends(get_readable_document_ids)):
    results = get_services()[1].retrieve(request.question, request.top_k, readable)
    return QuestionResponse(question=request.question, results=results)


@api_router.post('/ask', response_model=AnswerResponse, response_model_exclude_none=True)
def ask_question(request: QuestionRequest, generation=Depends(get_generation),
                 readable: list[str] | None = Depends(get_readable_document_ids),
                 user: User = Depends(get_current_user), settings: Settings = Depends(get_settings),
                 limiter: RateLimiter = Depends(get_rate_limiter)):
    limiter.check('chat', str(user.id), settings.rate_limit_chat_per_minute)
    logger.info('question received characters=%d', len(request.question))
    try:
        # Raw Top-K, not the similarity threshold: the LLM decides whether it supports an answer.
        contexts = get_services()[1].search_candidates(request.question, request.top_k, readable)
    except PipelineError:
        raise
    except Exception as exc:
        raise PipelineError('retrieval_failure', 'Không thể truy xuất tài liệu. Vui lòng thử lại sau.', 503) from exc
    logger.info('candidates=%d top_relevance=%s top_distance=%s', len(contexts),
                contexts[0].relevance if contexts else None, contexts[0].distance if contexts else None)
    return answer_from_contexts(request.question, contexts, generation)
