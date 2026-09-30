# Postponed annotations: the `list` method below would otherwise shadow the builtin in signatures.
from __future__ import annotations

import uuid

from sqlalchemy import ColumnElement, delete, func, or_, select, true
from sqlalchemy.orm import Session

from app.db.models import Chunk, Conversation, Document, DocumentStatus, DocumentSummary, Message, MessageRole, User


def visible_to(user: User | None) -> ColumnElement[bool]:
    """Documents a user may read: their own, shared ones and ownerless ones (always shared).
    None = no user filter (maintenance scripts)."""
    if user is None:
        return true()
    return or_(Document.owner_id == user.id, Document.is_shared.is_(True), Document.owner_id.is_(None))


class DocumentRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, document_id: uuid.UUID) -> Document | None:
        return self.session.get(Document, document_id)

    def get_visible(self, document_id: uuid.UUID, user: User | None) -> Document | None:
        return self.session.scalar(select(Document).where(Document.id == document_id, visible_to(user)))

    def get_by_sha256(self, sha256: str, owner_id: uuid.UUID | None = None) -> Document | None:
        """Duplicates are per owner, so an upload never reveals another user's private file."""
        owner = Document.owner_id.is_(None) if owner_id is None else Document.owner_id == owner_id
        return self.session.scalar(select(Document).where(Document.sha256 == sha256, owner))

    def list(self, page: int, page_size: int, query: str | None = None,
             user: User | None = None) -> tuple[list[Document], int]:
        """Newest first; `query` matches the original filename case-insensitively."""
        condition = visible_to(user)
        if query:
            condition = condition & Document.filename.ilike(f'%{_escape_like(query)}%', escape='\\')
        total = self.session.scalar(select(func.count()).select_from(Document).where(condition)) or 0
        items = self.session.scalars(select(Document).where(condition)
                                     .order_by(Document.created_at.desc(), Document.id)
                                     .offset((page - 1) * page_size).limit(page_size)).all()
        return list(items), total

    def visible_ready(self, user: User | None) -> list[Document]:
        return list(self.session.scalars(select(Document).where(visible_to(user),
                                                                Document.status == DocumentStatus.ready)
                                         .order_by(Document.created_at.desc())))

    def add(self, document: Document) -> Document:
        self.session.add(document)
        self.session.flush()
        return document

    def delete(self, document: Document) -> None:
        self.session.delete(document)
        self.session.flush()

    def replace_chunks(self, document_id: uuid.UUID, chunks: list[Chunk]) -> None:
        """New content (re-index) also invalidates the cached summary, which was built from the old chunks."""
        self.session.execute(delete(Chunk).where(Chunk.document_id == document_id))
        self.session.execute(delete(DocumentSummary).where(DocumentSummary.document_id == document_id))
        self.session.add_all(chunks)
        self.session.flush()

    def chunks(self, document_id: uuid.UUID) -> list[Chunk]:
        return list(self.session.scalars(select(Chunk).where(Chunk.document_id == document_id)
                                         .order_by(Chunk.ordinal)))

    def chunks_by_ids(self, chunk_ids: list[uuid.UUID]) -> dict[uuid.UUID, Chunk]:
        if not chunk_ids:
            return {}
        return {chunk.id: chunk for chunk in self.session.scalars(select(Chunk).where(Chunk.id.in_(chunk_ids)))}


class ConversationRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, conversation_id: uuid.UUID, owner_id: uuid.UUID | None = None) -> Conversation | None:
        """With `owner_id`, only that user's conversation (others' look nonexistent)."""
        conversation = self.session.get(Conversation, conversation_id)
        if conversation is None or (owner_id is not None and conversation.owner_id != owner_id):
            return None
        return conversation

    def create(self, title: str, owner_id: uuid.UUID | None = None) -> Conversation:
        conversation = Conversation(title=title, owner_id=owner_id)
        self.session.add(conversation)
        self.session.flush()
        return conversation

    def list(self, page: int, page_size: int, owner_id: uuid.UUID | None = None) -> tuple[list[Conversation], int]:
        condition = Conversation.owner_id == owner_id if owner_id is not None else true()
        total = self.session.scalar(select(func.count()).select_from(Conversation).where(condition)) or 0
        items = self.session.scalars(select(Conversation).where(condition)
                                     .order_by(Conversation.updated_at.desc(), Conversation.id)
                                     .offset((page - 1) * page_size).limit(page_size)).all()
        return list(items), total

    def add_message(self, conversation: Conversation, role: MessageRole, content: str,
                    sources: list | None = None) -> Message:
        message = Message(conversation_id=conversation.id, role=role, content=content, sources=sources)
        self.session.add(message)
        conversation.updated_at = func.now()
        self.session.flush()
        return message

    def history(self, conversation_id: uuid.UUID, limit: int) -> list[Message]:
        """The last `limit` messages, oldest first."""
        recent = self.session.scalars(select(Message).where(Message.conversation_id == conversation_id)
                                      .order_by(Message.seq.desc()).limit(limit)).all()
        return list(reversed(recent))

    def delete(self, conversation: Conversation) -> None:
        self.session.delete(conversation)
        self.session.flush()


def _escape_like(text: str) -> str:
    return text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


class UserRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, user_id: uuid.UUID) -> User | None:
        return self.session.get(User, user_id)

    def by_username(self, username: str) -> User | None:
        return self.session.scalar(select(User).where(func.lower(User.username) == username.lower()))

    def list(self) -> list[User]:
        return list(self.session.scalars(select(User).order_by(User.username)))

    def count(self) -> int:
        return self.session.scalar(select(func.count()).select_from(User)) or 0

    def add(self, user: User) -> User:
        self.session.add(user)
        self.session.flush()
        return user
