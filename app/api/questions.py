import logging
from fastapi import APIRouter, Depends
from app.core.dependencies import get_services, get_generation
from app.core.errors import PipelineError
from app.models.schemas import QuestionRequest, QuestionResponse, AnswerResponse
from app.services.answering import answer_from_contexts

router = APIRouter(prefix='/questions', tags=['questions'])
api_router = APIRouter(prefix='/api/questions', tags=['questions'])
logger = logging.getLogger(__name__)


@router.post('/search', response_model=QuestionResponse)
def search_question(request: QuestionRequest):
    results = get_services()[1].retrieve(request.question, request.top_k)
    return QuestionResponse(question=request.question, results=results)


@api_router.post('/ask', response_model=AnswerResponse, response_model_exclude_none=True)
def ask_question(request: QuestionRequest, generation=Depends(get_generation)):
    logger.info('question received characters=%d', len(request.question))
    try:
        # Raw Top-K, not the similarity threshold: the LLM decides whether it supports an answer.
        contexts = get_services()[1].search_candidates(request.question, request.top_k)
    except PipelineError:
        raise
    except Exception as exc:
        raise PipelineError('retrieval_failure', 'Không thể truy xuất tài liệu. Vui lòng thử lại sau.', 503) from exc
    logger.info('candidates=%d top_relevance=%s top_distance=%s', len(contexts),
                contexts[0].relevance if contexts else None, contexts[0].distance if contexts else None)
    return answer_from_contexts(request.question, contexts, generation)
