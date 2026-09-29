"""Reproduce upload -> raw Top-K -> RetrievalService -> /api/questions/ask on a separate Chroma DB.

Synthetic PDF plus the project's own evaluation documents as distractors. Never touches
data/chroma or data/uploads. Prints chunk previews of synthetic/evaluation text only.
"""
import argparse
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

FACT = ('Bài toán Alpha yêu cầu hệ thống xử lý tài liệu PDF.\n'
        'Mã kiểm thử của bài toán Alpha là ZXQ-91827.')
QUESTIONS = ['Mã kiểm thử của bài toán Alpha là gì?', 'Bài toán Beta có mã gì?']
FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'


def make_pdf(path: Path, text: str = FACT):
    import fitz
    with fitz.open() as pdf:
        page = pdf.new_page()
        font = {'fontname': 'dejavu', 'fontfile': FONT} if Path(FONT).is_file() else {}
        page.insert_text((60, 100), text, fontsize=13, **font)
        pdf.save(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, default=Path('.cache/repro') / uuid4().hex)
    parser.add_argument('--no-distractors', action='store_true')
    args = parser.parse_args()
    # Must be set before app settings are created.
    os.environ['CHROMA_PATH'] = str(args.work / 'chroma')
    os.environ['UPLOAD_DIR'] = str(args.work / 'uploads')
    from fastapi.testclient import TestClient
    from app.core.config import get_settings
    from app.core.dependencies import get_services
    from app.core.logging import configure_logging
    from app.main import app
    configure_logging()
    get_settings.cache_clear()
    settings = get_settings()
    args.work.mkdir(parents=True, exist_ok=True)
    pdf = args.work / 'alpha_synthetic.pdf'
    make_pdf(pdf)
    report = {'chroma_path': str(settings.chroma_path), 'threshold': settings.retrieval_threshold,
              'top_k': settings.top_k, 'uploads': [], 'questions': []}
    files = [pdf] if args.no_distractors else [pdf, *(Path('evaluation/documents') / name for name in
                                                      ('hr.docx', 'operations.xlsx', 'travel.pdf'))]
    with TestClient(app) as client:
        for path in files:
            response = client.post('/api/documents/upload', files={'file': (path.name, path.read_bytes())})
            report['uploads'].append({'file': path.name, 'status': response.status_code, 'body': response.json()})
        ingestion, retrieval = get_services()
        report['collection'] = ingestion.store.collection.name
        report['collection_count'] = ingestion.store.collection.count()
        for question in QUESTIONS:
            hits = retrieval.store.search(retrieval.embedding.embed_query(question), settings.top_k)
            raw = [{'rank': rank, 'id': f"{hit.metadata.get('document_id', '')[:12]}_{hit.metadata.get('chunk_index')}",
                    'filename': hit.metadata.get('filename'), 'page': hit.metadata.get('page'),
                    'preview': hit.content[:120].replace('\n', ' '), 'distance': round(hit.distance, 6),
                    'relevance': round(hit.relevance, 6)} for rank, hit in enumerate(hits, 1)]
            filtered = retrieval.retrieve(question)
            response = client.post('/api/questions/ask', json={'question': question})
            report['questions'].append({'question': question, 'raw_top_k': raw,
                                        'threshold_filtered_count': len(filtered),
                                        'ask_status': response.status_code, 'ask_body': response.json()})
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
