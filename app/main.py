import logging
from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from app.api import documents, questions
from app.core.config import get_settings
from app.core.dependencies import get_generation
from app.core.errors import PipelineError
from app.core.logging import configure_logging, log_failure

configure_logging()
logger = logging.getLogger(__name__)
app = FastAPI(title='Multi-modal Document Assistant', version='2.0.0',
              description='Local extraction/OCR and retrieval; a local Ollama model answers from retrieved text only.')
app.add_middleware(CORSMiddleware,
                   allow_origins=get_settings().allowed_frontend_origins,
                   allow_credentials=False, allow_methods=['POST'],
                   allow_headers=['Content-Type'])
app.include_router(documents.router)
app.include_router(questions.router)
app.include_router(documents.api_router)
app.include_router(questions.api_router)


@app.exception_handler(PipelineError)
async def pipeline_error_handler(request: Request, exc: PipelineError):
    log_failure(logger, exc.code, exc)
    return JSONResponse(status_code=exc.status_code, content={'error': exc.code, 'detail': str(exc)})


@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception):
    log_failure(logger, 'internal_error', exc)
    return JSONResponse(status_code=500, content={'error': 'internal_error', 'detail': 'Check server logs.'})


@app.get('/health')
def health():
    # Liveness only; model downloads are lazy and are not triggered here.
    return {'status': 'ok', 'phase': 'generation'}


@app.get('/health/ollama')
def ollama_health(generation=Depends(get_generation)):
    # GET {OLLAMA_BASE_URL}/api/tags and confirm OLLAMA_MODEL is pulled; failures return 503.
    return generation.client.health()
