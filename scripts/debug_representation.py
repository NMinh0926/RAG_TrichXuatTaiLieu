"""Local-only representation experiment; never imports generation or mutates production."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import resource
import statistics
import time
import unicodedata
from uuid import uuid4

from scripts.compare_embeddings import fingerprint, write_json, distribution


def normalize(text):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFC', text)).strip()


def prepare(work):
    import chromadb
    from chromadb.config import Settings as ChromaSettings
    from app.core.config import Settings
    from app.services.embedding import EmbeddingService
    from app.services.file_router import load_file
    from app.services.ocr_service import OCRService
    from app.services.text_cleaner import clean_text
    settings = Settings()
    model = EmbeddingService(settings.embedding_model).model
    client = chromadb.PersistentClient(str(work / 'production_snapshot'), settings=ChromaSettings(anonymized_telemetry=False))
    coll = client.get_collection('documents_85d5decab4479bc4')
    records = coll.get(include=['documents', 'metadatas', 'embeddings'])
    chunks = [dict(id=i, raw_content=t, metadata=m, vector=v.tolist()) for i,t,m,v in
              zip(records['ids'], records['documents'], records['metadatas'], records['embeddings'])]
    chunks.sort(key=lambda c: (c['metadata']['filename'], c['metadata']['chunk_index']))
    report = []
    for chunk in chunks:
        meta = chunk['metadata']
        siblings = [c for c in chunks if c['metadata']['document_id'] == meta['document_id']]
        idx = siblings.index(chunk)
        report.append(dict(id=chunk['id'], filename=meta['filename'], page=meta.get('page'),
                           chunk_index=meta['chunk_index'], token_count=len(model.tokenizer(chunk['raw_content'])['input_ids']),
                           first_300_chars=chunk['raw_content'][:300],
                           previous_chunk_id=siblings[idx-1]['id'] if idx else None,
                           next_chunk_id=siblings[idx+1]['id'] if idx+1 < len(siblings) else None))
    write_json(work / 'chunk_inspection.json', report)
    ocr = OCRService(settings.ocr_lang)
    docs = []
    for doc_id in sorted({c['metadata']['document_id'] for c in chunks}):
        name = next(c['metadata']['filename'] for c in chunks if c['metadata']['document_id'] == doc_id)
        matches = [p for p in Path('data/uploads').glob('*') if p.is_file() and
                   hashlib.sha256(name.encode()+b'\0'+p.read_bytes()).hexdigest() == doc_id]
        if not matches:
            raise ValueError(f'No original upload for {name}')
        path = matches[0]
        units = load_file(path, ocr, settings.pdf_min_text_chars)
        raw_units = [u.model_dump() for u in units]
        for u in units:
            u.content = clean_text(u.content)
            u.metadata['filename'] = name
        docs.append(dict(document_id=doc_id, filename=name, source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                         raw_units=raw_units, units=[u.model_dump() for u in units]))
    frozen = dict(documents=docs, current_chunks=chunks, top_k=4, baseline_threshold=.55)
    frozen['sha256'] = fingerprint(frozen)
    if (work / 'inputs.json').exists():
        raise ValueError('Inputs already frozen')
    write_json(work / 'inputs.json', frozen)
    print(json.dumps({'chunks': len(chunks), 'documents': len(docs), 'sha256': frozen['sha256']}, indent=2))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare', 'run'])
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--variant', default='current')
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare(args.work)
    else:
        run(args.work, args.variant)


if __name__ == '__main__':
    main()
