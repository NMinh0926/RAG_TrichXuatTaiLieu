"""Health checks (unauthenticated, for Docker/monitoring) and the system status page data (signed-in users)."""
import time
from collections.abc import Callable

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text

from app.core.auth import get_current_user
from app.core.config import get_settings
from app.core.dependencies import get_job_queue, get_llm, get_services, get_storage, get_summary_llm
from app.core.errors import PipelineError
from app.db.models import Document, IngestJob, User, UserRole
from app.db.repositories import visible_to
from app.db.session import get_sessionmaker

health_router = APIRouter(tags=['health'])
system_router = APIRouter(prefix='/api/system', tags=['system'])


def probe(check: Callable[[], object]) -> dict:
    """Run one dependency check; failures report an error code only (no hostnames or credentials)."""
    start = time.perf_counter()
    try:
        check()
        result = {'ok': True}
    except PipelineError as exc:
        result = {'ok': False, 'error': exc.code}
    except Exception as exc:
        result = {'ok': False, 'error': type(exc).__name__}
    return {**result, 'ms': round((time.perf_counter() - start) * 1000, 1)}


def ping_database() -> None:
    with get_sessionmaker()() as session:
        session.execute(text('SELECT 1'))


def dependency_checks() -> dict[str, dict]:
    return {'database': probe(ping_database), 'object_storage': probe(lambda: get_storage().ping()),
            'vector_store': probe(lambda: get_services()[1].store.collection.count()),
            'queue': probe(lambda: get_job_queue().ping())}


@health_router.get('/health')
def health():
    """Liveness only; model downloads are lazy and are not triggered here."""
    return {'status': 'ok'}


@health_router.get('/health/ready')
def ready():
    """Readiness: PostgreSQL, object storage, Chroma and Redis reachable (503 otherwise). The LLM is
    reported separately by /health/llm because documents can still be uploaded while it is down."""
    checks = dependency_checks()
    healthy = all(check['ok'] for check in checks.values())
    return JSONResponse({'status': 'ok' if healthy else 'unavailable', 'checks': checks},
                        status_code=200 if healthy else 503)


@health_router.get('/health/llm')
def llm_health(llm=Depends(get_llm), summary_llm=Depends(get_summary_llm)):
    """GET {LLM_BASE_URL}/models and confirm LLM_MODEL is available; failures return 503. When LLM_SUMMARY_MODEL is
    set it is looked up in the same model list: questions still work without it, so a missing summary model only
    turns the status to 'degraded' (whole-document summaries would fail)."""
    result = llm.health()
    response = {'status': result['status'], 'model': result['model'], 'model_available': result['model_available']}
    if summary_llm is not None:
        available = model_listed(summary_llm.model, result.get('models', []))
        response.update(summary_model=summary_llm.model, summary_model_available=available)
        if not available:
            response['status'] = 'degraded'
    return response


def model_listed(model: str, models: list[str]) -> bool:
    """Same rule as LLMClient.health: an untagged name also matches its ':latest' tag."""
    return model in models or (':' not in model and f'{model}:latest' in models)


def worker_status() -> dict:
    from rq import Worker
    queue = get_job_queue().queue
    return {'workers': len(Worker.all(connection=queue.connection)), 'queued_jobs': queue.count,
            'failed_jobs': queue.failed_job_registry.count}


@system_router.get('/status')
def status(user: User = Depends(get_current_user)):
    """Everything the status page shows: dependencies, LLM, worker/queue, library counts and key settings.
    Admins get whole-library counts; other users only counts of documents they may read."""
    settings = get_settings()
    checks = dependency_checks()
    models: list[str] = []
    checks['llm'] = probe(lambda: models.extend(get_llm().health().get('models', [])))
    summary_llm = get_summary_llm()
    if summary_llm is not None:
        # Same server as LLM_MODEL: reuse its model list instead of a second request.
        if not checks['llm']['ok']:
            checks['llm_summary'] = dict(checks['llm'])
        elif model_listed(summary_llm.model, models):
            checks['llm_summary'] = {'ok': True}
        else:
            checks['llm_summary'] = {'ok': False, 'error': 'llm_model_missing'}
    worker = None
    if checks['queue']['ok']:
        try:
            worker = worker_status()
        except Exception as exc:
            checks['worker'] = {'ok': False, 'error': type(exc).__name__}
    if worker is not None:
        checks['worker'] = {'ok': worker['workers'] > 0, **({} if worker['workers'] else {'error': 'no_worker'})}
    library = jobs = None
    if checks['database']['ok']:
        scope = visible_to(None if user.role == UserRole.admin else user)
        with get_sessionmaker()() as session:
            library = {status.value: count for status, count in session.execute(
                select(Document.status, func.count()).where(scope).group_by(Document.status))}
            jobs = {status.value: count for status, count in session.execute(
                select(IngestJob.status, func.count()).join(Document, IngestJob.document_id == Document.id)
                .where(scope).group_by(IngestJob.status))}
    return {'status': 'ok' if all(check['ok'] for check in checks.values()) else 'degraded', 'checks': checks,
            'queue': worker, 'documents_by_status': library, 'jobs_by_status': jobs,
            'config': {'llm_model': settings.llm_model, 'llm_summary_model': settings.llm_summary_model or None,
                       'embedding_model': settings.embedding_model, 'reranker_model': settings.reranker_model or None,
                       'top_k': settings.top_k, 'chunk_size': settings.chunk_size, 'ocr_lang': settings.ocr_lang,
                       'max_upload_mb': settings.max_upload_mb, 'max_pdf_pages': settings.max_pdf_pages}}
