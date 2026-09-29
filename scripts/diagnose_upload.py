"""Local upload/search diagnostic in an isolated Chroma DB. Never calls the LLM."""
import argparse
import json
import os
from pathlib import Path
from uuid import uuid4


def diagnose(file=None, question=None, preview=False):
    root = (Path('.cache/upload-diagnostic') / uuid4().hex).resolve()
    root.mkdir(parents=True)
    os.environ['CHROMA_PATH'] = str(root / 'chroma')
    os.environ['UPLOAD_DIR'] = str(root / 'uploads')
    # Set paths before loading production config/dependencies.
    from docx import Document
    import numpy as np
    from fastapi.testclient import TestClient
    from app.core.config import get_settings
    from app.core.dependencies import get_services, get_generation
    from app.services.file_router import load_file
    from app.services.text_cleaner import clean_text
    from app.services.chunking import chunk_units
    from app.services.generation import GeneratedAnswer
    from app.main import app

    if file is None:
        file = root / 'single_fact.docx'
        document = Document()
        document.add_paragraph('Nhân viên chính thức được nghỉ phép 12 ngày mỗi năm.')
        document.save(file)
        question = question or 'Một năm nhân viên được nghỉ bao nhiêu ngày phép?'
    settings = get_settings()
    ingestion, retrieval = get_services()
    store = ingestion.store
    units = load_file(file, ingestion.ocr, settings.pdf_min_text_chars)
    unit_stats = []
    for unit in units:
        before = len(unit.content)
        unit.content = clean_text(unit.content)
        unit_stats.append({'chars_before': before, 'chars_after': len(unit.content),
                           'metadata': unit.metadata})
    model = ingestion.embedding.model
    chunks = chunk_units(units, model.tokenizer, settings.chunk_size,
                         settings.chunk_overlap, model.max_seq_length)
    received = []
    class ContextSpy:
        def answer(self, question, contexts):
            received.extend(contexts)
            return GeneratedAnswer(supported=True, answer='DIAGNOSTIC_CONTEXT_RECEIVED (not a generated answer)')
    app.dependency_overrides[get_generation] = ContextSpy
    try:
        with TestClient(app) as client:
            count_before = store.collection.count()
            with file.open('rb') as stream:
                uploaded = client.post('/api/documents/upload', files={'file': (file.name, stream)})
            saved = store.collection.get(include=['documents', 'metadatas', 'embeddings'])
            vectors = np.asarray(saved['embeddings'])
            query_vector = ingestion.embedding.embed_query(question)
            raw = store.search(query_vector, 10)
            filtered = client.post('/questions/search', json={'question': question})
            asked = client.post('/api/questions/ask', json={'question': question})
            outside = client.post('/questions/search', json={'question': 'CEO của Microsoft là ai?'})
        result = {
            'question': question, 'upload_status': uploaded.status_code, 'upload': uploaded.json(),
            'store': {'upload_path': str(ingestion.settings.chroma_path.resolve()),
                      'retrieval_path': str(retrieval.settings.chroma_path.resolve()),
                      'same_instance': store is retrieval.store, 'collection': store.collection.name,
                      'tenant': store.client.tenant, 'database': store.client.database,
                      'count_before': count_before, 'count_after': store.collection.count()},
            'units': unit_stats, 'chunks': [{'index': c.chunk_index, 'characters': len(c.content),
                'metadata': c.metadata, **({'preview': c.content[:100]} if preview else {})} for c in chunks],
            'stored_ids': saved['ids'], 'stored_chunks_match': sorted(saved['documents']) == sorted(c.content for c in chunks),
            'embeddings': {'shape': list(vectors.shape), 'finite': bool(np.isfinite(vectors).all()),
                           'query_dimension': len(query_vector),
                           'norms': np.linalg.norm(vectors, axis=1).tolist() if vectors.ndim == 2 else []},
            'threshold': settings.retrieval_threshold,
            'raw': [{'distance': h.distance, 'relevance': h.relevance, 'metadata': h.metadata,
                     **({'preview': h.content[:100]} if preview else {})} for h in raw],
            'filtered_count': len(filtered.json().get('results', [])),
            'ask_generation_mode': 'context_spy_no_network_not_live_llm',
            'ask_status': asked.status_code, 'ask_response': asked.json(), 'context_received': len(received),
            'outside_filtered_count': len(outside.json().get('results', [])),
        }
        return result
    finally:
        app.dependency_overrides.clear()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--file', type=Path)
    parser.add_argument('--question')
    parser.add_argument('--preview', action='store_true', help='Include at most 100 characters per chunk')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.file and not args.question:
        parser.error('--question is required with --file')
    result = diagnose(args.file, args.question, args.preview)
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    print(encoded)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding='utf-8')


if __name__ == '__main__':
    main()
