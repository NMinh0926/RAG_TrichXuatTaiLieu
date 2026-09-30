import hashlib
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.core.errors import PipelineError
from app.models.schemas import Chunk, DocumentUnit, IngestionResponse
from app.services.chunking import chunk_units
from app.services.file_router import extension_of, load_file, validate_extension
from app.services.text_cleaner import clean_text

logger = logging.getLogger(__name__)
# PDFs are limited by MAX_PDF_PAGES at upload; other formats get the same budget as text, at this many characters
# per page, so one long DOCX/XLSX cannot hold the worker for up to the job timeout.
CHARS_PER_PAGE = 5000


@dataclass
class ProcessedDocument:
    units: list[DocumentUnit]
    chunks: list[Chunk]
    chunk_ids: list[str]
    page_count: int | None


class IngestionService:
    """Extract/OCR → clean → chunk → embed → vector upsert. Knows nothing about SQL or object storage."""

    def __init__(self, settings, ocr, embedding, store):
        self.settings, self.ocr, self.embedding, self.store = settings, ocr, embedding, store

    def process(self, data: bytes, filename: str, document_id: str, id_prefix: str | None = None,
                progress: Callable[[str, float], None] | None = None,
                warn: Callable[[dict], None] | None = None) -> ProcessedDocument:
        """Chunk ids are new UUIDs (database rows) or '<id_prefix>_<n>' (stable, database-less re-ingest).

        `progress(step, fraction)` reports extract/chunk/embed advancement; `warn` receives non-fatal
        per-page problems (e.g. one page failing OCR) instead of failing the whole document.
        """
        report = progress or (lambda step, fraction=0.0: None)
        validate_extension(filename)
        if not data:
            raise PipelineError('empty_document', 'File trống hoặc không tồn tại.')
        if len(data) > self.settings.max_upload_mb * 1024 * 1024:
            raise PipelineError('file_too_large', f'File vượt quá giới hạn {self.settings.max_upload_mb} MB '
                                                  '(MAX_UPLOAD_MB).', 413)
        report('extract', 0.0)
        try:
            units = load_file(data, filename, self.ocr, self.settings.pdf_min_text_chars,
                              progress=lambda fraction: report('extract', fraction), warn=warn,
                              workers=self.settings.ocr_workers)
        except PipelineError:
            raise
        except Exception as exc:
            raise PipelineError('extraction_failure', 'Không đọc được file; file có thể bị hỏng hoặc được đặt '
                                                     'mật khẩu.') from exc
        pages = [unit.metadata['page'] for unit in units if isinstance(unit.metadata.get('page'), int)]
        logger.info('extracted units=%d filename=%r', len(units), filename)
        for unit in units:
            cleaned = clean_text(unit.content)
            if cleaned != unit.content:
                # Offsets would no longer match the word positions: keep the text, drop the boxes.
                unit.content, unit.layout = cleaned, None
        units = [unit for unit in units if any(char.isalnum() for char in unit.content)]
        if not units:
            raise PipelineError('no_usable_text', 'Không tìm thấy nội dung văn bản sau khi trích xuất/OCR.')
        limit = self.settings.max_pdf_pages * CHARS_PER_PAGE
        if extension_of(filename) != '.pdf' and (size := sum(len(unit.content) for unit in units)) > limit:
            raise PipelineError('file_too_large', f'Tài liệu quá dài ({size} ký tự); giới hạn là {limit} ký tự, '
                                                  'tương đương MAX_PDF_PAGES trang.', 413)
        report('chunk')
        model = self.embedding.model
        chunks = chunk_units(units, model.tokenizer, self.settings.chunk_size,
                             self.settings.chunk_overlap, model.max_seq_length)
        if not chunks:
            raise PipelineError('no_usable_text', 'Không tạo được đoạn văn bản nào từ nội dung tài liệu.')
        report('embed', 0.0)
        embeddings = self.embedding.embed_documents([chunk.content for chunk in chunks],
                                                    progress=lambda fraction: report('embed', fraction))
        logger.info('chunks=%d embeddings=%d dimension=%d filename=%r', len(chunks), len(embeddings),
                    len(embeddings[0]) if embeddings else 0, filename)
        chunk_ids = [f'{id_prefix}_{c.chunk_index}' if id_prefix else str(uuid.uuid4()) for c in chunks]
        self.store.add(document_id, chunks, embeddings, ids=chunk_ids)
        return ProcessedDocument(units, chunks, chunk_ids, max(pages) if pages else None)

    def ingest(self, path: Path, filename: str | None = None) -> IngestionResponse:
        """Index a local file without a database (evaluation/benchmark scripts)."""
        filename = filename or path.name
        data = path.read_bytes() if path.is_file() else b''
        digest = hashlib.sha256(filename.encode('utf-8') + b'\0' + data).hexdigest()
        processed = self.process(data, filename, digest, id_prefix=digest)
        return IngestionResponse(document_id=digest, filename=filename, units=len(processed.units),
                                 chunks=len(processed.chunks))
