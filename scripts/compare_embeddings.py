"""Offline A/B dense retrieval. Frozen chunks; separate test DBs; never calls an LLM.

Keep --work outside production Chroma and exclude its private input snapshot from
submission. Only sanitized scores/metadata (never document text) go to --results.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import statistics
import threading
import time
from uuid import uuid4

MODELS = {
    'current': 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2',
    'bge-m3': 'BAAI/bge-m3',
    'e5-base': 'intfloat/multilingual-e5-base',
}


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def fingerprint(data):
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def prepare(work, pdf):
    from app.core.config import Settings
    from app.services.embedding import EmbeddingService
    from app.services.ocr_service import OCRService
    from app.services.file_router import load_file
    from app.services.text_cleaner import clean_text
    from app.services.chunking import chunk_units

    target = work / 'frozen_inputs.json'
    if target.exists():
        raise ValueError('Frozen inputs already exist; use a new experiment directory.')
    settings = Settings()
    embedding = EmbeddingService(settings.embedding_model)
    model = embedding.model
    ocr = OCRService(settings.ocr_lang)
    cases_path = Path('evaluation/questions.json')
    cases = json.loads(cases_path.read_text(encoding='utf-8'))
    corpora = {}
    source_hashes = {}
    def freeze(paths):
        frozen = []
        for path, name in paths:
            source_hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
            units = load_file(path, ocr, settings.pdf_min_text_chars)
            for unit in units:
                unit.content = clean_text(unit.content)
                unit.metadata['filename'] = name
            chunks = chunk_units(units, model.tokenizer, settings.chunk_size,
                                 settings.chunk_overlap, model.max_seq_length)
            frozen.extend(c.model_dump() for c in chunks)
        return frozen
    names = sorted({c['expected_source']['filename'] for c in cases if c['expected_source']})
    corpora['evaluation'] = {'cases': cases, 'chunks': freeze([
        (Path('evaluation/documents') / name, name) for name in names])}
    if pdf:
        corpora['supplemental'] = {
            'cases': [
                {'id': 'user-1', 'question': 'bài toán 1 là gì', 'expected_keywords': ['RAG'],
                 'expected_source': {'filename': 'user_document.pdf', 'page': 1}},
                {'id': 'user-2', 'question': 'bài toán 2 là gì', 'expected_keywords': ['Meeting', 'Audio'],
                 'expected_source': {'filename': 'user_document.pdf', 'page': 1}},
                {'id': 'user-3', 'question': 'bài toán 3 là gì', 'expected_keywords': [],
                 'expected_source': None},
            ],
            'chunks': freeze([(pdf, 'user_document.pdf')]),
        }
    result = {'created_at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
              'chunk_model': settings.embedding_model,
              'chunk_size': settings.chunk_size, 'chunk_overlap': settings.chunk_overlap,
              'chunk_model_max_length': model.max_seq_length,
              'top_k': settings.top_k, 'baseline_threshold': settings.retrieval_threshold,
              'dataset_sha256': hashlib.sha256(cases_path.read_bytes()).hexdigest(),
              'source_sha256': source_hashes, 'corpora': corpora}
    result['input_sha256'] = fingerprint(result)
    write_json(target, result)
    print(json.dumps({'prepared': str(target), 'input_sha256': result['input_sha256'],
                      'corpora': {k: {'chunks': len(v['chunks']), 'questions': len(v['cases'])}
                                  for k, v in corpora.items()}}, indent=2), flush=True)


def distribution(values):
    import numpy as np
    values = [v for v in values if v is not None]
    return {'count': len(values), **{name: float(np.quantile(values, q)) if values else None
            for name, q in [('min', 0), ('p25', .25), ('median', .5), ('p75', .75), ('max', 1)]}}


def metrics(rows, threshold):
    positives = [r for r in rows if r['answerable']]
    negatives = [r for r in rows if not r['answerable']]
    hits = sum(r['best_relevant_score'] is not None and r['best_relevant_score'] >= threshold for r in positives)
    fp = sum(r['top_score'] is not None and r['top_score'] >= threshold for r in negatives)
    return {'threshold': threshold, 'answerable_hit_count': hits, 'answerable_count': len(positives),
            'retrieval_hit_rate': hits / len(positives) if positives else None,
            'no_answer_false_positive': fp, 'no_answer_count': len(negatives),
            'no_answer_empty': len(negatives) - fp}


def run_model(work, results, variant):
    import numpy as np
    import psutil
    import torch
    from huggingface_hub import snapshot_download
    from sentence_transformers import SentenceTransformer
    from app.models.schemas import Chunk
    from app.services.vector_store import VectorStore
    from scripts.evaluate import contains_keywords, source_matches

    source = work / 'frozen_inputs.json'
    inputs = json.loads(source.read_text(encoding='utf-8'))
    digest = inputs.pop('input_sha256')
    if fingerprint(inputs) != digest:
        raise ValueError('Frozen input fingerprint mismatch')
    inputs['input_sha256'] = digest
    model_name = MODELS[variant]
    model_db = work / 'databases' / variant / uuid4().hex
    if model_db.exists():
        raise ValueError('Model test DB already exists; do not overwrite it.')
    torch.set_num_threads(4)
    torch.set_num_interop_threads(1)
    process = psutil.Process()
    initial_rss = process.memory_info().rss
    peak = [initial_rss]
    stop = threading.Event()
    def monitor():
        while not stop.wait(.05):
            peak[0] = max(peak[0], process.memory_info().rss)
    thread = threading.Thread(target=monitor, daemon=True)
    thread.start()
    started = time.perf_counter()
    print(f'Downloading/checking cache: {model_name}', flush=True)
    snapshot = snapshot_download(model_name, allow_patterns=[
        '*.json', '*.safetensors', 'sentencepiece.bpe.model', 'tokenizer.model', '*.txt'], max_workers=2)
    if not list(Path(snapshot).glob('*.safetensors')):
        snapshot = snapshot_download(model_name, revision=Path(snapshot).name,
                                     allow_patterns=['pytorch_model.bin', 'pytorch_model-*.bin'], max_workers=2)
    download_seconds = time.perf_counter() - started
    started = time.perf_counter()
    model = SentenceTransformer(snapshot, device='cpu', local_files_only=True, trust_remote_code=False)
    load_seconds = time.perf_counter() - started
    rss_loaded = process.memory_info().rss
    print(f'Loaded: {model_name} in {load_seconds:.2f}s', flush=True)
    def encode(texts, query=False):
        # E5 requires these prefixes even for non-English inputs. BGE-M3 does not.
        if variant == 'e5-base':
            texts = [('query: ' if query else 'passage: ') + text for text in texts]
        return model.encode(texts, batch_size=4, normalize_embeddings=True,
                            show_progress_bar=False, convert_to_numpy=True)
    result = {'variant': variant, 'model': model_name, 'revision': Path(snapshot).name,
              'input_sha256': digest, 'dataset_sha256': inputs['dataset_sha256'],
              'top_k': inputs['top_k'], 'baseline_threshold': inputs['baseline_threshold'],
              'encoding': 'query:/passage: prefixes' if variant == 'e5-base' else 'no instruction/prefix',
              'device': 'cpu', 'dtype': str(next(model.parameters()).dtype),
              'threads': 4, 'batch_size': 4, 'corpora': {}}
    doc_timings = []; query_timings = []; total_doc_count = 0
    all_doc_texts = []
    for corpus_name, corpus in inputs['corpora'].items():
        chunks = [Chunk(**c) for c in corpus['chunks']]
        texts = [c.content for c in chunks]
        # Preserve production's current encoding exactly, including any truncation
        # caused by retokenizing a text slice. New models must fit the frozen text.
        formatted = [('passage: ' + t) if variant == 'e5-base' else t for t in texts]
        token_lengths = [len(model.tokenizer(t, truncation=False)['input_ids']) for t in formatted]
        truncated_count = sum(n > model.max_seq_length for n in token_lengths)
        if truncated_count and variant != 'current':
            raise ValueError('Candidate would truncate frozen chunks; comparison aborted')
        started = time.perf_counter(); vectors = encode(texts); elapsed = time.perf_counter() - started
        doc_timings.append(elapsed); total_doc_count += len(texts); all_doc_texts.extend(texts)
        store = VectorStore(model_db / corpus_name, model_name,
                            inputs['chunk_size'], inputs['chunk_overlap'])
        # Preserve the exact per-document metadata and chunk indices from baseline.
        for filename in sorted({c.metadata['filename'] for c in chunks}):
            indices = [i for i,c in enumerate(chunks) if c.metadata['filename'] == filename]
            store.add(hashlib.sha256(filename.encode()).hexdigest(), [chunks[i] for i in indices],
                      vectors[indices].tolist())
        rows = []
        for case in corpus['cases']:
            q = case['question']
            formatted_q = 'query: ' + q if variant == 'e5-base' else q
            if len(model.tokenizer(formatted_q, truncation=False)['input_ids']) > model.max_seq_length:
                raise ValueError('Query truncation would invalidate comparison')
            started = time.perf_counter(); query_vector = encode([q], query=True)[0]
            query_timings.append(time.perf_counter() - started)
            hits = store.search(query_vector.tolist(), inputs['top_k'])
            entries = []
            for rank, hit in enumerate(hits, 1):
                relevant = bool(source_matches(hit.metadata, case['expected_source']) and
                                contains_keywords(hit.content, case['expected_keywords']))
                entries.append({'rank': rank, 'distance': hit.distance, 'relevance': hit.relevance,
                                'metadata': hit.metadata, 'expected_fact_match': relevant})
            relevant_scores = [h['relevance'] for h in entries if h['expected_fact_match']]
            rows.append({'id': case['id'], 'question': q, 'answerable': case['expected_source'] is not None,
                         'expected_source': case['expected_source'], 'top_k': entries,
                         'top_score': entries[0]['relevance'] if entries else None,
                         'best_relevant_score': max(relevant_scores) if relevant_scores else None})
        positive = distribution([r['best_relevant_score'] for r in rows if r['answerable']])
        negative = distribution([r['top_score'] for r in rows if not r['answerable']])
        result['corpora'][corpus_name] = {
            'chunks_sha256': fingerprint(corpus['chunks']), 'chunk_count': len(chunks),
            'max_candidate_input_tokens': max(token_lengths), 'embedding_dimension': vectors.shape[1],
            'model_max_sequence_length': model.max_seq_length,
            'chunks_exceeding_model_token_limit': truncated_count,
            'maximum_tokens_truncated': max(0, max(token_lengths) - model.max_seq_length),
            'collection': store.collection.name, 'test_db': str(model_db / corpus_name),
            'raw_hit_count': sum(r['best_relevant_score'] is not None for r in rows if r['answerable']),
            'at_baseline_threshold': metrics(rows, inputs['baseline_threshold']),
            'relevant_distribution': positive, 'no_answer_distribution': negative,
            'separation_gap': positive['min'] - negative['max'] if positive['min'] is not None and negative['max'] is not None else None,
            'cases': rows,
        }
    # Tune only AFTER unfiltered evaluation, on the unchanged 25-question corpus.
    rows = result['corpora']['evaluation']['cases']
    sweep = [metrics(rows, round(i / 100, 2)) for i in range(101)]
    zero_fp = [m for m in sweep if m['no_answer_false_positive'] == 0]
    chosen = sorted(zero_fp, key=lambda m: (-m['answerable_hit_count'], m['threshold']))[0]
    baseline_metrics = metrics(rows, inputs['baseline_threshold'])
    if (variant == 'current' and baseline_metrics['no_answer_false_positive'] == 0
            and baseline_metrics['answerable_hit_count'] == chosen['answerable_hit_count']):
        chosen = metrics(rows, inputs['baseline_threshold'])  # retain established baseline if equally good
    result['calibration'] = {'policy': 'max recall subject to zero no-answer FP; 0.01 grid; evaluation corpus only',
                             'recommended': chosen, 'sweep': sweep,
                             'not_independent_validation': True}
    if 'supplemental' in result['corpora']:
        result['corpora']['supplemental']['at_recommended_threshold'] = metrics(
            result['corpora']['supplemental']['cases'], chosen['threshold'])
    # Steady-state throughput is measured separately from first inference/load/download.
    warm_docs = []
    for _ in range(3):
        started = time.perf_counter(); encode(all_doc_texts); warm_docs.append(time.perf_counter() - started)
    stop.set(); thread.join()
    peak_rss = max(peak[0], resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024)
    files = [p for p in Path(snapshot).rglob('*') if p.is_file()]
    result['resources'] = {
        'download_or_cache_check_seconds': download_seconds, 'cached_model_load_seconds': load_seconds,
        'rss_before_model_bytes': initial_rss, 'rss_after_load_bytes': rss_loaded, 'peak_process_rss_bytes': peak_rss,
        'first_document_encoding_seconds': sum(doc_timings), 'document_count': total_doc_count,
        'warm_documents_seconds_median': statistics.median(warm_docs),
        'warm_documents_per_second': total_doc_count / statistics.median(warm_docs),
        'query_latency_seconds_median': statistics.median(query_timings),
        'query_latency_seconds_p95': float(np.quantile(query_timings, .95)),
        'query_count': len(query_timings), 'snapshot_bytes': sum(p.stat().st_size for p in files),
    }
    write_json(results / f'{variant}.json', result)
    print(json.dumps({'model': model_name, 'baseline': result['corpora']['evaluation']['at_baseline_threshold'],
                      'calibrated': chosen, 'resources': result['resources']}, indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'run'])
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--results', type=Path, default=Path('evaluation/embedding_ab'))
    parser.add_argument('--pdf', type=Path)
    parser.add_argument('--model', choices=list(MODELS))
    args = parser.parse_args()
    work = args.work.resolve()
    production = Path(os.getenv('CHROMA_PATH', 'data/chroma')).resolve()
    if work == production or production in work.parents or work in production.parents:
        parser.error('Experiment directory must be completely separate from production Chroma.')
    if args.action == 'prepare':
        prepare(work, args.pdf)
    else:
        if not args.model:
            parser.error('--model is required for run')
        run_model(work, args.results, args.model)


if __name__ == '__main__':
    main()
