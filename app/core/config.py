from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from dotenv import load_dotenv

# Model libraries read cache variables directly from os.environ.
load_dotenv(override=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')
    # Metadata database (documents, chunks, jobs, conversations).
    database_url: str = 'postgresql+psycopg://rag:rag@localhost:5432/rag'
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
    max_upload_mb: int = Field(25, ge=1)
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

    @model_validator(mode='after')
    def validate_overlap(self):
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError('CHUNK_OVERLAP must be smaller than CHUNK_SIZE')
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
