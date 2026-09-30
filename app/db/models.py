"""PostgreSQL schema. Vectors live in Chroma with ids equal to Chunk.id."""
import enum
import uuid
from datetime import datetime

from sqlalchemy import (JSON, BigInteger, Boolean, CheckConstraint, DateTime, Enum, ForeignKey, Identity, Index,
                        Integer, String, Text, UniqueConstraint, func, text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# JSONB on PostgreSQL; plain JSON elsewhere.
Json = JSON().with_variant(JSONB(), 'postgresql')


class Base(DeclarativeBase):
    pass


class DocumentStatus(str, enum.Enum):
    pending = 'pending'
    processing = 'processing'
    ready = 'ready'
    failed = 'failed'


class JobStatus(str, enum.Enum):
    queued = 'queued'
    running = 'running'
    succeeded = 'succeeded'
    failed = 'failed'


class MessageRole(str, enum.Enum):
    user = 'user'
    assistant = 'assistant'


class UserRole(str, enum.Enum):
    admin = 'admin'
    user = 'user'


def _enum(kind: type[enum.Enum], name: str) -> Enum:
    return Enum(kind, name=name, values_callable=lambda members: [member.value for member in members])


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(),
                                                 onupdate=func.now())


class User(TimestampMixin, Base):
    """Accounts are created by an admin (API or scripts/create_user.py); no self sign-up."""
    __tablename__ = 'users'
    # Usernames are stored lower-case; the index also rejects case variants written by other means.
    __table_args__ = (Index('uq_users_username_lower', text('lower(username)'), unique=True),)

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(_enum(UserRole, 'user_role'), default=UserRole.user)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Bumped on password change / deactivation: invalidates every issued token.
    token_version: Mapped[int] = mapped_column(Integer, default=0)


class Document(TimestampMixin, Base):
    __tablename__ = 'documents'

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # NULL owner: uploaded before accounts existed or by maintenance scripts; always shared, managed by admins.
    # RESTRICT: deleting a user must never turn their private documents into ownerless (shared) ones.
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('users.id', ondelete='RESTRICT'), index=True)
    # Shared documents are visible to every user; others only to their owner. Defaults to shared exactly when
    # there is no owner (the CHECK constraint below forbids ownerless private rows).
    is_shared: Mapped[bool] = mapped_column(
        Boolean, default=lambda context: context.get_current_parameters().get('owner_id') is None)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(127))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    # Unique per owner: identical files of different users must not reveal each other.
    sha256: Mapped[str] = mapped_column(String(64))
    page_count: Mapped[int | None] = mapped_column(Integer)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    object_key: Mapped[str] = mapped_column(String(512))
    status: Mapped[DocumentStatus] = mapped_column(_enum(DocumentStatus, 'document_status'),
                                                   default=DocumentStatus.pending)
    error_message: Mapped[str | None] = mapped_column(Text)

    chunks: Mapped[list['Chunk']] = relationship(back_populates='document', cascade='all, delete-orphan',
                                                 passive_deletes=True, order_by='Chunk.ordinal')
    jobs: Mapped[list['IngestJob']] = relationship(back_populates='document', cascade='all, delete-orphan',
                                                   passive_deletes=True)

    __table_args__ = (Index('ix_documents_filename', 'filename'), Index('ix_documents_created_at', 'created_at'),
                      UniqueConstraint('owner_id', 'sha256', name='uq_documents_owner_sha256',
                                       postgresql_nulls_not_distinct=True),
                      CheckConstraint('owner_id IS NOT NULL OR is_shared', name='ck_documents_ownerless_shared'))


class Chunk(Base):
    __tablename__ = 'chunks'

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('documents.id', ondelete='CASCADE'))
    ordinal: Mapped[int] = mapped_column(Integer)
    page: Mapped[int | None] = mapped_column(Integer)
    sheet: Mapped[str | None] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text)
    # Character range of the chunk inside its page/sheet text.
    char_start: Mapped[int] = mapped_column(Integer)
    char_end: Mapped[int] = mapped_column(Integer)
    # [{"page": n, "x0":…, "y0":…, "x1":…, "y1":…}] in PDF points (filled by the ingest pipeline).
    bboxes: Mapped[list | None] = mapped_column(Json)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    document: Mapped[Document] = relationship(back_populates='chunks')

    __table_args__ = (UniqueConstraint('document_id', 'ordinal', name='uq_chunks_document_ordinal'),)


class IngestJob(Base):
    __tablename__ = 'ingest_jobs'

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('documents.id', ondelete='CASCADE'), index=True)
    status: Mapped[JobStatus] = mapped_column(_enum(JobStatus, 'job_status'), default=JobStatus.queued)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    step: Mapped[str] = mapped_column(String(64), default='queued')
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    # Non-fatal problems, e.g. [{"page": 3, "error": "ocr_failure"}].
    warnings: Mapped[list | None] = mapped_column(Json)
    # Seconds per step: {"download": .., "extract": .., "chunk": .., "embed": .., "index": ..}.
    timings: Mapped[dict | None] = mapped_column(Json)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    document: Mapped[Document] = relationship(back_populates='jobs')


class DocumentSummary(Base):
    """Cached map-reduce summary; regenerated when the model or prompt version changes."""
    __tablename__ = 'document_summaries'

    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('documents.id', ondelete='CASCADE'), primary_key=True)
    model: Mapped[str] = mapped_column(String(255))
    prompt_version: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Conversation(TimestampMixin, Base):
    __tablename__ = 'conversations'

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    title: Mapped[str] = mapped_column(String(255))

    messages: Mapped[list['Message']] = relationship(back_populates='conversation', cascade='all, delete-orphan',
                                                     passive_deletes=True, order_by='Message.seq')


class Message(Base):
    __tablename__ = 'messages'

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # Insertion order; timestamps tie inside one transaction.
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), unique=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('conversations.id', ondelete='CASCADE'),
                                                       index=True)
    role: Mapped[MessageRole] = mapped_column(_enum(MessageRole, 'message_role'))
    content: Mapped[str] = mapped_column(Text)
    # Citations of an assistant answer: [{"n": 1, "chunk_id":…, "document_id":…, "page":…, "excerpt":…}].
    sources: Mapped[list | None] = mapped_column(Json)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    conversation: Mapped[Conversation] = relationship(back_populates='messages')
