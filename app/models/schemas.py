import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.layout import Placed


class DocumentUnit(BaseModel):
    content: str
    metadata: dict[str, str | int | float | bool | None]
    # Positioned words of `content` (PDF pages only), used to compute chunk bounding boxes.
    layout: list[Placed] | None = Field(default=None, exclude=True, repr=False)


class Chunk(BaseModel):
    content: str
    metadata: dict[str, str | int | float | bool | None]
    chunk_index: int
    # Character range inside the unit (page/sheet) text.
    char_start: int = 0
    char_end: int = 0
    # [{"page", "x0", "y0", "x1", "y1"}] in PDF points, one rectangle per line; None when unknown.
    bboxes: list[dict] | None = None


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


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    status: str
    progress: int
    step: str
    error_code: str | None
    error_message: str | None
    warnings: list[dict] | None
    timings: dict[str, float] | None = None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


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
    latest_job: JobOut | None = None
    # NULL owner: uploaded before accounts existed (shared).
    owner_id: uuid.UUID | None = None
    is_shared: bool = False
    # Whether the requesting user may share, re-index or delete it.
    can_manage: bool = False


class DocumentUpdate(BaseModel):
    is_shared: bool


class SubmissionOut(BaseModel):
    document: DocumentOut
    job: JobOut | None
    # True when an identical file (same SHA-256) already exists; nothing new was queued.
    duplicate: bool


class DocumentList(BaseModel):
    items: list[DocumentOut]
    total: int
    page: int
    page_size: int


class ChatRequest(QuestionRequest):
    conversation_id: uuid.UUID | None = None
    # Restrict retrieval/summaries to these documents; empty means all documents.
    document_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)


class CitedSource(BaseModel):
    n: int
    chunk_id: str | None
    document_id: str | None
    filename: str | None
    page: int | None = None
    sheet: str | None = None
    excerpt: str | None = None
    # Highlight rectangles in PDF points: [{"page", "x0", "y0", "x1", "y1"}].
    bboxes: list[dict] | None = None


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: str
    content: str
    sources: list[CitedSource] | None
    created_at: datetime


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime


class ConversationDetail(ConversationOut):
    messages: list[MessageOut]


class ConversationList(BaseModel):
    items: list[ConversationOut]
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


USERNAME_PATTERN = r'^[A-Za-z0-9._-]{3,64}$'


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    role: str
    is_active: bool
    created_at: datetime


class SessionOut(BaseModel):
    user: UserOut
    # For API clients (Authorization: Bearer); browsers use the HttpOnly cookie set by the same response.
    access_token: str
    expires_in: int


class UserCreate(BaseModel):
    username: str = Field(pattern=USERNAME_PATTERN)
    password: str = Field(min_length=8, max_length=256)
    role: str = Field('user', pattern='^(admin|user)$')


class UserUpdate(BaseModel):
    password: str | None = Field(None, min_length=8, max_length=256)
    role: str | None = Field(None, pattern='^(admin|user)$')
    is_active: bool | None = None


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)
