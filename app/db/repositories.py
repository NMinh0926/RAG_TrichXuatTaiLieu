# Postponed annotations: the `list` method below would otherwise shadow the builtin in signatures.
from __future__ import annotations

import uuid

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.models import Chunk, Document


class DocumentRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, document_id: uuid.UUID) -> Document | None:
        return self.session.get(Document, document_id)

    def get_by_sha256(self, sha256: str) -> Document | None:
        return self.session.scalar(select(Document).where(Document.sha256 == sha256))

    def list(self, page: int, page_size: int, query: str | None = None) -> tuple[list[Document], int]:
        """Newest first; `query` matches the original filename case-insensitively."""
        condition = Document.filename.ilike(f'%{_escape_like(query)}%', escape='\\') if query else True
        total = self.session.scalar(select(func.count()).select_from(Document).where(condition)) or 0
        items = self.session.scalars(select(Document).where(condition)
                                     .order_by(Document.created_at.desc(), Document.id)
                                     .offset((page - 1) * page_size).limit(page_size)).all()
        return list(items), total

    def add(self, document: Document) -> Document:
        self.session.add(document)
        self.session.flush()
        return document

    def delete(self, document: Document) -> None:
        self.session.delete(document)
        self.session.flush()

    def replace_chunks(self, document_id: uuid.UUID, chunks: list[Chunk]) -> None:
        self.session.execute(delete(Chunk).where(Chunk.document_id == document_id))
        self.session.add_all(chunks)
        self.session.flush()

    def chunks(self, document_id: uuid.UUID) -> list[Chunk]:
        return list(self.session.scalars(select(Chunk).where(Chunk.document_id == document_id)
                                         .order_by(Chunk.ordinal)))

    def chunks_by_ids(self, chunk_ids: list[uuid.UUID]) -> dict[uuid.UUID, Chunk]:
        if not chunk_ids:
            return {}
        return {chunk.id: chunk for chunk in self.session.scalars(select(Chunk).where(Chunk.id.in_(chunk_ids)))}


def _escape_like(text: str) -> str:
    return text.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
