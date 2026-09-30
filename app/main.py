import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import auth, chat, documents, questions, system
from app.core.config import get_settings
from app.core.errors import PipelineError
from app.core.logging import configure_logging, log_failure, request_id

configure_logging()
logger = logging.getLogger(__name__)
access_logger = logging.getLogger('app.access')
app = FastAPI(title='Multi-modal Document Assistant', version='4.0.0',
              description='Documents in PostgreSQL + S3 storage; retrieval over Chroma; an OpenAI-compatible LLM '
                          '(local Ollama by default) answers from retrieved text only. Sign in via /api/auth/login.')
app.add_middleware(CORSMiddleware,
                   allow_origins=get_settings().allowed_frontend_origins,
                   allow_credentials=False, allow_methods=['GET', 'POST', 'PATCH', 'DELETE'],
                   allow_headers=['Content-Type', 'Authorization'])
for router in (questions.router, documents.api_router, documents.jobs_router, questions.api_router,
               chat.chat_router, chat.conversations_router, auth.auth_router, auth.users_router,
               system.health_router, system.system_router):
    app.include_router(router)


@app.middleware('http')
async def request_context(request: Request, call_next):
    """Request id (from nginx `X-Request-ID` or new) on every log line and response; one access log per request."""
    incoming = request.headers.get('x-request-id', '')
    rid = incoming if 0 < len(incoming) <= 64 and incoming.isascii() and incoming.isprintable() else uuid.uuid4().hex
    token = request_id.set(rid)
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        response.headers['X-Request-ID'] = rid
        return response
    finally:
        if not request.url.path.startswith('/health'):
            access_logger.info('request', extra={'method': request.method, 'path': request.url.path,
                                                 'status': status,
                                                 'duration_ms': round((time.perf_counter() - start) * 1000, 1),
                                                 'user_id': getattr(request.state, 'user_id', None)})
        request_id.reset(token)


@app.exception_handler(PipelineError)
async def pipeline_error_handler(request: Request, exc: PipelineError):
    if exc.status_code >= 500:
        log_failure(logger, exc.code, exc)
    else:
        logger.warning('request rejected', extra={'error': exc.code, 'status': exc.status_code})
    return JSONResponse(status_code=exc.status_code, content={'error': exc.code, 'detail': str(exc)})


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception):
    log_failure(logger, 'internal_error', exc)
    detail = 'Lỗi máy chủ không mong muốn; vui lòng thử lại hoặc báo quản trị viên.'
    return JSONResponse(status_code=500, content={'error': 'internal_error', 'detail': detail})
