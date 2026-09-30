"""Ingest benchmark over the size corpus (make_corpus): per file total and per-step time, pages/s,
peak container RAM/CPU and the storage each file adds to PostgreSQL, object storage and Chroma.

Uses the scratch database/bucket and a local Chroma directory (scripts.isolation); jobs run inline in this
process through the real upload endpoint, so the numbers match the worker minus queue latency.
Run: python -m scripts.benchmark.bench_corpus --corpus .cache/benchmark/corpus --output docs/benchmarks/stage5_ingest.json
"""
import argparse
import json
import os
import platform
import sys
import time
from pathlib import Path

from sqlalchemy import text

from scripts.benchmark.resources import Sampler, directory_bytes

TABLES = ('documents', 'chunks', 'ingest_jobs')


def postgres_bytes(session) -> dict[str, int]:
    return {table: session.scalar(text('SELECT pg_total_relation_size(:t)'), {'t': table}) for table in TABLES}


def bucket_bytes(storage, bucket: str) -> int:
    total = 0
    for page in storage.client.get_paginator('list_objects_v2').paginate(Bucket=bucket):
        total += sum(item['Size'] for item in page.get('Contents', []))
    return total


def clear(client) -> None:
    while items := client.get('/api/documents', params={'page_size': 100}).json()['items']:
        for item in items:
            client.delete(f"/api/documents/{item['id']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--corpus', type=Path, default=Path('.cache/benchmark/corpus'))
    parser.add_argument('--work', type=Path, default=Path('.cache/benchmark/corpus_run'))
    parser.add_argument('--output', type=Path, default=Path('docs/benchmarks/stage5_ingest.json'))
    parser.add_argument('--files', nargs='*', help='file names from the manifest (default: all, smallest first)')
    args = parser.parse_args()
    manifest = json.loads((args.corpus / 'manifest.json').read_text(encoding='utf-8'))
    rows = sorted((row for row in manifest if not args.files or row['file'] in args.files),
                  key=lambda row: (row['bytes'], row['kind']))
    # The benchmark measures files above the production upload limit on purpose.
    os.environ['MAX_UPLOAD_MB'] = str(max(row['bytes'] for row in rows) // 2**20 + 10)
    from scripts.isolation import isolate, prepare
    isolate(args.work)
    prepare()
    import torch
    from fastapi.testclient import TestClient
    from app.core.config import get_settings
    from app.core.dependencies import get_services, get_storage
    from app.core.logging import configure_logging
    from app.db.session import get_sessionmaker
    from app.main import app
    configure_logging()
    settings = get_settings()
    storage, session_factory = get_storage(), get_sessionmaker()
    chroma_path = Path(settings.chroma_path)
    result = {'python': platform.python_version(), 'cpu_count': os.cpu_count(), 'torch': torch.__version__,
              'embedding_device': 'cuda' if torch.cuda.is_available() else 'cpu',
              'config': {'embedding_model': settings.embedding_model, 'chunk_size': settings.chunk_size,
                         'chunk_overlap': settings.chunk_overlap, 'ocr_lang': settings.ocr_lang,
                         'ocr_workers': settings.ocr_workers},
              'files': []}
    with TestClient(app) as client:
        clear(client)
        start = time.perf_counter()
        ingestion, _ = get_services()
        ingestion.embedding.embed_documents(['khởi động'])  # load the model outside the timed uploads
        result['model_load_seconds'] = round(time.perf_counter() - start, 1)
        for row in rows:
            path = args.corpus / row['file']
            with session_factory() as session:
                before_pg = postgres_bytes(session)
            before_s3, before_chroma = bucket_bytes(storage, settings.s3_bucket), directory_bytes(chroma_path)
            with Sampler() as sampler:
                start = time.perf_counter()
                response = client.post('/api/documents', files={'file': (row['file'], path.read_bytes(),
                                                                         'application/pdf')})
                seconds = time.perf_counter() - start
            body = response.json()
            if response.status_code != 202:
                result['files'].append({**row, 'status': response.status_code, 'error': body})
                print(f"{row['file']}: HTTP {response.status_code} {body}", flush=True)
                continue
            job = client.get(f"/api/jobs/{body['job']['id']}").json()
            document = client.get(f"/api/documents/{body['document']['id']}").json()
            with session_factory() as session:
                after_pg = postgres_bytes(session)
            pages = document['page_count'] or 0
            entry = {**row, 'status': job['status'], 'error_code': job['error_code'],
                     'seconds': round(seconds, 1), 'steps': job['timings'], 'pages_ingested': pages,
                     'chunks': document['chunk_count'], 'warnings': len(job['warnings'] or []),
                     'pages_per_second': round(pages / seconds, 2) if seconds else None,
                     'megabytes_per_minute': round(row['bytes'] / 1e6 / seconds * 60, 1),
                     **sampler.summary(),
                     'storage_bytes': {'postgres': sum(after_pg.values()) - sum(before_pg.values()),
                                       'postgres_by_table': {t: after_pg[t] - before_pg[t] for t in TABLES},
                                       'object_storage': bucket_bytes(storage, settings.s3_bucket) - before_s3,
                                       'chroma': directory_bytes(chroma_path) - before_chroma}}
            result['files'].append(entry)
            print(f"{row['file']}: {entry['status']} {entry['seconds']}s pages={pages} chunks={entry['chunks']} "
                  f"steps={entry['steps']} peak={entry['peak_memory_mb']}MB cpu={entry['cpu_cores_average']}",
                  flush=True)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        clear(client)
    return 0


if __name__ == '__main__':
    sys.exit(main())
