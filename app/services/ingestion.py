import hashlib
import logging
from pathlib import Path

from app.core.errors import PipelineError
from app.models.schemas import IngestionResponse
from app.services.chunking import chunk_units
from app.services.file_router import load_file, validate_extension
from app.services.text_cleaner import clean_text

logger = logging.getLogger(__name__)


class IngestionService:
    def __init__(self, settings, ocr, embedding, store):
        self.settings, self.ocr, self.embedding, self.store = settings, ocr, embedding, store

    def ingest(self, path: Path, filename: str | None = None) -> IngestionResponse:
        validate_extension(path)
        filename = filename or path.name
        if not path.is_file() or path.stat().st_size == 0:
            raise PipelineError('empty_document', 'File is missing or empty.')
        if path.stat().st_size > self.settings.max_upload_mb * 1024 * 1024:
            raise PipelineError('file_too_large', 'File exceeds MAX_UPLOAD_MB.', 413)
        digest = hashlib.sha256()
        digest.update(filename.encode('utf-8'))
        digest.update(b'\0')
        with path.open('rb') as source:
            for block in iter(lambda: source.read(1024 * 1024), b''):
                digest.update(block)
        document_id = digest.hexdigest()
        try:
            units = load_file(path, self.ocr, self.settings.pdf_min_text_chars)
        except PipelineError:
            raise
        except Exception as exc:
            raise PipelineError('extraction_failure', 'Cannot extract file; it may be corrupt or encrypted.') from exc
        logger.info('extracted units=%d filename=%r', len(units), filename)
        for unit in units:
            length_before = len(unit.content)
            unit.content = clean_text(unit.content)
            unit.metadata['filename'] = filename
            logger.debug('document unit filename=%r page=%s sheet=%s source_type=%s chars_before=%d chars_after=%d',
                         filename, unit.metadata.get('page'), unit.metadata.get('sheet'),
                         unit.metadata.get('source_type'), length_before, len(unit.content))
        units = [unit for unit in units if any(char.isalnum() for char in unit.content)]
        if not units:
            raise PipelineError('no_usable_text', 'No usable text found after extraction/OCR.')
        model = self.embedding.model
        chunks = chunk_units(units, model.tokenizer, self.settings.chunk_size,
                             self.settings.chunk_overlap, model.max_seq_length)
        if not chunks:
            raise PipelineError('no_usable_text', 'No usable text chunks found.')
        embeddings = self.embedding.embed_documents([chunk.content for chunk in chunks])
        logger.info('chunks generated=%d filename=%r', len(chunks), filename)
        logger.info('embeddings generated=%d dimension=%d filename=%r', len(embeddings),
                    len(embeddings[0]) if embeddings else 0, filename)
        self.store.add(document_id, chunks, embeddings)
        logger.info('vectors stored=%d filename=%r', len(embeddings), filename)
        return IngestionResponse(document_id=document_id, filename=filename, units=len(units), chunks=len(chunks))
