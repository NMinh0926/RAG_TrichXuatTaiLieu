"""Ingest benchmark: per-step timings with the production services, on an isolated Chroma DB.

Order: (1) cold end-to-end upload through the real API (models load lazily, like a fresh
backend), (2) warm per-step timings reusing the loaded models, (3) warm end-to-end upload.
Never touches data/chroma or data/uploads.
Run: python -m scripts.benchmark.bench_ingest --pdf .cache/benchmark/test_4mb.pdf
"""
import argparse
import json
import os
import platform
import resource
import statistics
import sys
import time
from pathlib import Path


def rss_mb() -> float:
    # Linux ru_maxrss is in KiB: peak resident memory of this process so far.
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)


class TimedOCR:
    """Wraps OCRService to time each page OCR call without changing its behavior."""

    def __init__(self, ocr):
        self.ocr, self.calls = ocr, []

    def extract(self, image):
        start = time.perf_counter()
        text = self.ocr.extract(image)
        self.calls.append(round(time.perf_counter() - start, 3))
        return text


def clear(client) -> None:
    """Delete every document in the scratch database so the next upload is not a SHA-256 duplicate."""
    while items := client.get('/api/documents', params={'page_size': 100}).json()['items']:
        for item in items:
            client.delete(f"/api/documents/{item['id']}")


def upload(client, pdf: Path) -> dict:
    clear(client)
    start = time.perf_counter()
    response = client.post('/api/documents/upload', files={'file': (pdf.name, pdf.read_bytes(), 'application/pdf')})
    return {'seconds': round(time.perf_counter() - start, 2), 'status': response.status_code, 'body': response.json()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pdf', type=Path, default=Path('.cache/benchmark/test_4mb.pdf'))
    parser.add_argument('--work', type=Path, default=Path('.cache/benchmark/run'))
    parser.add_argument('--output', type=Path, default=Path('docs/benchmarks/baseline_ingest.json'))
    args = parser.parse_args()
    # Scratch database/bucket/Chroma dir; must be set before app settings are created.
    from scripts.isolation import isolate, prepare
    isolate(args.work)
    prepare()
    started = time.perf_counter()
    from fastapi.testclient import TestClient
    from app.core.config import get_settings
    from app.core.dependencies import get_services
    from app.core.logging import configure_logging
    from app.main import app
    from app.services.chunking import chunk_units
    from app.services.pdf_loader import load_pdf
    from app.services.text_cleaner import clean_text
    from app.services.vector_store import VectorStore
    configure_logging()
    get_settings.cache_clear()
    settings = get_settings()
    import_seconds = round(time.perf_counter() - started, 2)
    result = {'pdf': {'path': str(args.pdf), 'megabytes': round(args.pdf.stat().st_size / 1e6, 2)},
              'python': platform.python_version(), 'cpu_count': os.cpu_count(),
              'config': {'embedding_model': settings.embedding_model, 'chunk_size': settings.chunk_size,
                         'chunk_overlap': settings.chunk_overlap, 'ocr_lang': settings.ocr_lang,
                         'pdf_min_text_chars': settings.pdf_min_text_chars},
              'import_seconds': import_seconds}

    with TestClient(app) as client:
        # 1. Cold: first upload loads the embedding model and the OCR engine inside the request.
        result['e2e_cold_upload'] = upload(client, args.pdf)
        ingestion, _ = get_services()

        # 2. Warm per-step timings with the same loaded models.
        steps = {}
        ocr = TimedOCR(ingestion.ocr)
        start = time.perf_counter()
        units = load_pdf(args.pdf.read_bytes(), args.pdf.name, ocr, settings.pdf_min_text_chars)
        read_total = time.perf_counter() - start
        steps['pdf_read_and_render'] = round(read_total - sum(ocr.calls), 2)
        steps['ocr'] = round(sum(ocr.calls), 2)
        start = time.perf_counter()
        for unit in units:
            unit.content = clean_text(unit.content)
        steps['clean'] = round(time.perf_counter() - start, 3)
        model = ingestion.embedding.model
        start = time.perf_counter()
        chunks = chunk_units(units, model.tokenizer, settings.chunk_size, settings.chunk_overlap, model.max_seq_length)
        steps['chunk'] = round(time.perf_counter() - start, 2)
        start = time.perf_counter()
        vectors = ingestion.embedding.embed_documents([chunk.content for chunk in chunks])
        steps['embedding'] = round(time.perf_counter() - start, 2)
        store = VectorStore(args.work / 'steps_chroma', settings.embedding_model, settings.chunk_size,
                            settings.chunk_overlap)
        start = time.perf_counter()
        store.add('benchmark-steps', chunks, vectors)
        steps['chroma_write'] = round(time.perf_counter() - start, 2)
        steps['total'] = round(sum(v for v in steps.values()), 2)
        tokens = [len(model.tokenizer(c.content, add_special_tokens=False)['input_ids']) for c in chunks]
        result['warm_steps_seconds'] = steps
        result['counts'] = {
            'pages': len(units), 'ocr_pages': len(ocr.calls),
            'text_pages': sum(u.metadata.get('source_type') == 'text' for u in units),
            'characters': sum(len(u.content) for u in units), 'chunks': len(chunks),
            'chunk_tokens': {'min': min(tokens), 'median': statistics.median(tokens), 'max': max(tokens)},
            'effective_chunk_tokens': min(settings.chunk_size,
                                          model.max_seq_length - model.tokenizer.num_special_tokens_to_add(pair=False)),
            'embedding_dimension': len(vectors[0]),
        }
        result['ocr_seconds_per_page'] = ocr.calls
        result['throughput'] = {
            'ocr_seconds_per_page_median': statistics.median(ocr.calls) if ocr.calls else None,
            'embedding_chunks_per_second': round(len(chunks) / steps['embedding'], 1) if steps['embedding'] else None,
            'warm_pages_per_second': round(len(units) / steps['total'], 2),
        }
        # 3. Warm end-to-end through the API (same content -> same ids, full processing again),
        # with timing wrappers on the request's own OCR/embedding/Chroma calls.
        inside = TimedOCR(ingestion.ocr)
        ingestion.ocr = inside
        timings = {}

        def timed(name, function):
            def wrapper(*args, **kwargs):
                start = time.perf_counter()
                try:
                    return function(*args, **kwargs)
                finally:
                    timings[name] = round(timings.get(name, 0) + time.perf_counter() - start, 2)
            return wrapper
        ingestion.embedding.embed_documents = timed('embedding', ingestion.embedding.embed_documents)
        ingestion.store.add = timed('chroma_write', ingestion.store.add)
        result['e2e_warm_upload'] = upload(client, args.pdf)
        result['e2e_warm_upload']['inside_request_seconds'] = {
            'ocr': round(sum(inside.calls), 2), 'ocr_pages': len(inside.calls), **timings}
    result['model_load_seconds_estimate'] = round(result['e2e_cold_upload']['seconds'] - result['e2e_warm_upload']['seconds'], 2)
    result['peak_rss_mb'] = rss_mb()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'ocr_seconds_per_page'}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
