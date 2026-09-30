from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv
from sqlalchemy import URL

# Model libraries read cache variables directly from os.environ.
load_dotenv(override=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')
    # Metadata database (documents, chunks, jobs, conversations). DATABASE_URL wins when set (managed PostgreSQL);
    # otherwise the URL is built from the POSTGRES_* parts, so any password character is escaped correctly.
    database_url: str = ''
    postgres_user: str = 'rag'
    postgres_password: SecretStr = SecretStr('rag')
    postgres_db: str = 'rag'
    postgres_host: str = 'localhost'
    postgres_port: int = Field(5432, ge=1, le=65535)
    # Original files: any S3-compatible storage (SeaweedFS in compose, S3/R2 in deployment).
    s3_endpoint: str = 'http://localhost:8333'
    s3_access_key: SecretStr = SecretStr('')
    s3_secret_key: SecretStr = SecretStr('')
    s3_bucket: str = 'documents'
    s3_region: str = 'us-east-1'
    # Vectors: Chroma server when CHROMA_HOST is set, otherwise a local persistent directory.
    chroma_host: str = ''
    chroma_port: int = Field(8000, ge=1, le=65535)
    chroma_path: Path = Path('data/chroma')
    # Chosen by docs/benchmarks/stage2.md: bge-m3 with 512-token chunks, 6 chunks of context.
    embedding_model: str = 'BAAI/bge-m3'
    chunk_size: int = Field(512, ge=2)
    chunk_overlap: int = Field(64, ge=0)
    # Chunks given to the LLM. With a reranker, RETRIEVAL_CANDIDATES are reranked down to TOP_K.
    top_k: int = Field(6, ge=1, le=100)
    retrieval_candidates: int = Field(20, ge=1, le=200)
    reranker_model: str = ''
    # Legacy filter for /questions/search only. /api/questions/ask lets the LLM decide support.
    retrieval_threshold: float = Field(0.55, ge=-1, le=1)
    # Optional noise floor for answer candidates; -1 keeps the whole raw Top-K.
    candidate_min_relevance: float = Field(-1.0, ge=-1, le=1)
    # Background ingest queue (RQ). The legacy synchronous upload waits up to INGEST_WAIT_SECONDS.
    redis_url: str = 'redis://localhost:6379/0'
    ingest_job_timeout_seconds: int = Field(3600, ge=60)
    ingest_wait_seconds: int = Field(1800, ge=1)
    ocr_workers: int = Field(4, ge=1, le=32)
    # Tesseract languages, ISO or Tesseract codes joined with '+' (e.g. 'vi', 'vi+en').
    ocr_lang: str = 'vi'
    pdf_min_text_chars: int = Field(20, ge=1)
    # Upload limits from docs/benchmarks/report.md (ingest time grows with pages, not bytes).
    max_upload_mb: int = Field(100, ge=1)
    max_pdf_pages: int = Field(300, ge=1)
    # Session tokens are signed with AUTH_SECRET, or with the secret the init service stores in AUTH_SECRET_FILE.
    auth_secret: SecretStr = SecretStr('')
    auth_secret_file: Path = Path('/run/app-secrets/auth_secret')
    session_hours: int = Field(12, ge=1, le=24 * 30)
    # Send the session cookie only over HTTPS; enable when served behind TLS.
    cookie_secure: bool = False
    # Per user (or per IP for login) within a sliding minute; 0 disables a limit.
    rate_limit_login_per_minute: int = Field(10, ge=0)
    rate_limit_chat_per_minute: int = Field(20, ge=0)
    rate_limit_upload_per_minute: int = Field(20, ge=0)
    log_format: str = Field('json', pattern='^(json|text)$')
    # Any OpenAI-compatible server. Default: Ollama on the host (mapped in compose.yaml).
    llm_base_url: str = 'http://host.docker.internal:11434/v1'
    llm_api_key: SecretStr = SecretStr('')
    llm_model: str = 'qwen2.5:7b'
    # Optional faster model for the per-section (map) step of whole-document summaries; empty = LLM_MODEL.
    llm_summary_model: str = ''
    llm_timeout_seconds: float = Field(180, gt=0, le=1800)
    llm_max_tokens: int = Field(2048, ge=64, le=32768)
    llm_temperature: float = Field(0.2, ge=0, le=2)
    # Context window the server actually runs with (Ollama: OLLAMA_CONTEXT_LENGTH); used to size prompts.
    llm_context_tokens: int = Field(8192, ge=2048)
    frontend_origins: str = 'http://localhost:5173,http://127.0.0.1:5173'

    @property
    def allowed_frontend_origins(self) -> list[str]:
        return [origin.strip().rstrip('/') for origin in self.frontend_origins.split(',') if origin.strip()]

    def signing_secret(self) -> str:
        """AUTH_SECRET, else the generated secret file; refuse to run without one (tokens would be forgeable)."""
        secret = self.auth_secret.get_secret_value()
        if not secret and self.auth_secret_file.is_file():
            secret = self.auth_secret_file.read_text(encoding='utf-8').strip()
        if len(secret) < 32:
            raise RuntimeError('AUTH_SECRET is missing or shorter than 32 characters; set it in .env '
                               'or let the init service generate AUTH_SECRET_FILE.')
        return secret

    @model_validator(mode='after')
    def validate_overlap(self):
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError('CHUNK_OVERLAP must be smaller than CHUNK_SIZE')
        return self

    @model_validator(mode='after')
    def build_database_url(self):
        if not self.database_url.strip():
            self.database_url = URL.create(
                'postgresql+psycopg', username=self.postgres_user, password=self.postgres_password.get_secret_value(),
                host=self.postgres_host, port=self.postgres_port, database=self.postgres_db,
            ).render_as_string(hide_password=False)
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
