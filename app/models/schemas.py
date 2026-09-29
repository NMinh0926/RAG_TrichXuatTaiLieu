import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DocumentUnit(BaseModel):
    content: str
    metadata: dict[str, str | int | float | bool | None]


class Chunk(DocumentUnit):
    chunk_index: int
    # Character range inside the unit (page/sheet) text.
    char_start: int = 0
    char_end: int = 0


class SearchHit(DocumentUnit):
    # Vector id == chunks.id in PostgreSQL.
    id: str = ''
    distance: float
    relevance: float


class QuestionRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    top_k: int | None = Field(None, ge=1, le=100)

    @field_validator('question')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('Question must not be blank')
        return value.strip()


class QuestionResponse(BaseModel):
    question: str
    results: list[SearchHit]


class IngestionResponse(BaseModel):
    document_id: str
    filename: str
    units: int
    chunks: int


class UploadResponse(BaseModel):
    status: str = 'success'
    filename: str
    chunks_created: int
    document_id: uuid.UUID
    # True when an identical file (same SHA-256) was already stored; nothing was reprocessed.
    duplicate: bool = False


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    page_count: int | None
    chunk_count: int
    status: str
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class DocumentList(BaseModel):
    items: list[DocumentOut]
    total: int
    page: int
    page_size: int


class Source(BaseModel):
    filename: str
    page: int | None = None
    sheet: str | None = None


class AnswerResponse(BaseModel):
    answer: str
    sources: list[Source] = Field(default_factory=list)
