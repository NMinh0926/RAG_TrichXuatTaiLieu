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
    embedding_model: str = 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2'
    chunk_size: int = Field(700, ge=2)
    chunk_overlap: int = Field(120, ge=0)
    top_k: int = Field(4, ge=1, le=100)
    # Legacy filter for /questions/search only. /api/questions/ask lets the LLM decide support.
    retrieval_threshold: float = Field(0.55, ge=-1, le=1)
    # Optional noise floor for answer candidates; -1 keeps the whole raw Top-K.
    candidate_min_relevance: float = Field(-1.0, ge=-1, le=1)
    ocr_lang: str = 'vi'
    pdf_min_text_chars: int = Field(20, ge=1)
    max_upload_mb: int = Field(25, ge=1)
    # Ollama runs on the host; host.docker.internal is mapped in compose.yaml.
    ollama_base_url: str = 'http://host.docker.internal:11434'
    ollama_model: str = 'qwen2.5:7b'
    ollama_timeout_seconds: float = Field(120, gt=0, le=1800)
    ollama_temperature: float = Field(0.2, ge=0, le=2)
    ollama_num_ctx: int = Field(8192, ge=512)
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
