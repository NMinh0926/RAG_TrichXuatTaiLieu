from pydantic import BaseModel, Field, field_validator


class DocumentUnit(BaseModel):
    content: str
    metadata: dict[str, str | int | float | bool | None]


class Chunk(DocumentUnit):
    chunk_index: int


class SearchHit(DocumentUnit):
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


class Source(BaseModel):
    filename: str
    page: int | None = None
    sheet: str | None = None


class AnswerResponse(BaseModel):
    answer: str
    sources: list[Source] = Field(default_factory=list)
