"""Inspect unfiltered retrieval and threshold sweep; never edits the dataset/config."""
import json
from pathlib import Path
from uuid import uuid4

from app.core.config import Settings
from app.services.chunking import chunk_units
from app.services.embedding import EmbeddingService
from app.services.file_router import load_file
from app.services.ocr_service import OCRService
from app.services.text_cleaner import clean_text
from app.services.vector_store import VectorStore
from scripts.evaluate import contains_keywords, source_matches


def inspect():
    settings = Settings()
    root = Path('evaluation')
    cases = json.loads((root / 'questions.json').read_text(encoding='utf-8'))
    embedding = EmbeddingService(settings.embedding_model)
    ocr = OCRService(settings.ocr_lang)
    store = VectorStore(Path('.cache/evaluation') / uuid4().hex, settings.embedding_model,
                        settings.chunk_size, settings.chunk_overlap)
    extracted = {}
    for filename in sorted({c['expected_source']['filename'] for c in cases if c['expected_source']}):
        units = load_file(root / 'documents' / filename, ocr, settings.pdf_min_text_chars)
        for unit in units:
            unit.content = clean_text(unit.content)
        model = embedding.model
        chunks = chunk_units(units, model.tokenizer, settings.chunk_size, settings.chunk_overlap, model.max_seq_length)
        extracted[filename] = {'units': [u.model_dump() for u in units], 'chunks': [c.model_dump() for c in chunks]}
        store.add(filename, chunks, embedding.embed_documents([c.content for c in chunks]))
    rows = []
    for case in cases:
        hits = store.search(embedding.embed_query(case['question']), settings.top_k)
        rows.append({**case, 'raw_top_k': [hit.model_dump() for hit in hits]})
    sweep = []
    for threshold in [round(0.40 + i * 0.01, 2) for i in range(21)]:
        hit_count = empty = 0
        for row in rows:
            accepted = [h for h in row['raw_top_k'] if h['relevance'] >= threshold]
            if row['expected_source']:
                hit_count += any(source_matches(h['metadata'], row['expected_source']) and
                                 contains_keywords(h['content'], row['expected_keywords']) for h in accepted)
            else:
                empty += not accepted
        sweep.append({'threshold': threshold, 'hit_count': hit_count, 'no_answer_empty': empty})
    result = {'embedding_model': settings.embedding_model, 'top_k': settings.top_k,
              'extracted': extracted, 'cases': rows, 'sweep': sweep}
    (root / 'retrieval_diagnostic.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(sweep, indent=2))


if __name__ == '__main__':
    inspect()
