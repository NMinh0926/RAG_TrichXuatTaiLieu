"""Retrieval A/B: embedding model, chunk size and optional cross-encoder rerank, on the same extracted text.

Text is extracted/OCR'd once and shared; each configuration re-chunks with its own tokenizer, embeds into a
throwaway local Chroma directory and answers the same questions. A question is a hit when a chunk from the
expected source containing all expected keywords is in the final context passed to the LLM.
Run: python -m scripts.ab_retrieval --output docs/benchmarks/stage2_retrieval_ab.json
     [--extra-cases local_cases.json]   (same format as scripts.evaluate --extra-cases)
"""
import argparse
import gc
import json
import resource
import statistics
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from app.core.config import Settings
from app.services.chunking import chunk_units
from app.services.file_router import load_file
from app.services.ocr_service import OCRService
from app.services.text_cleaner import clean_text
from app.services.vector_store import VectorStore
from scripts.evaluate import contains_keywords, load_cases, source_matches

BENCHMARK_PDF = Path('.cache/benchmark/test_4mb.pdf')
BENCHMARK_CASES = [
    ('Thời gian thử việc đối với vị trí kỹ sư là bao lâu?', ['45 ngày'], 4),
    ('Nhân viên chính thức được nghỉ phép năm bao nhiêu ngày?', ['16 ngày'], 10),
    # contains_keywords strips thousands separators from the text, so keywords are written without them.
    ('Phụ cấp ăn trưa mỗi tháng là bao nhiêu?', ['850000'], 18),
    ('Mã quy trình phê duyệt công tác phí là gì?', ['CTP-2026-07'], 25),
]


@dataclass
class Config:
    name: str
    model: str
    chunk_size: int
    chunk_overlap: int
    context_k: int
    candidates: int
    reranker: str | None = None


CONFIGS = [
    Config('minilm_126_top4', 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2', 700, 120, 4, 4),
    Config('minilm_126_top20_rerank6', 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2', 700, 120, 6, 20,
           'BAAI/bge-reranker-v2-m3'),
    Config('bgem3_512_top6', 'BAAI/bge-m3', 512, 64, 6, 6),
    Config('bgem3_512_top20_rerank6', 'BAAI/bge-m3', 512, 64, 6, 20, 'BAAI/bge-reranker-v2-m3'),
]


def rss_mb() -> float:
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)


def extract(documents: list[tuple[Path, str]], ocr: OCRService, settings: Settings) -> dict[str, list]:
    corpus = {}
    for path, filename in documents:
        units = load_file(path.read_bytes(), filename, ocr, settings.pdf_min_text_chars)
        for unit in units:
            unit.content = clean_text(unit.content)
        corpus[filename] = [unit for unit in units if any(char.isalnum() for char in unit.content)]
    return corpus


def run_config(config: Config, corpus: dict[str, list], cases: list[dict], work: Path) -> dict:
    from sentence_transformers import CrossEncoder, SentenceTransformer
    start = time.perf_counter()
    model = SentenceTransformer(config.model, device='cpu')
    reranker = CrossEncoder(config.reranker, max_length=512, device='cpu') if config.reranker else None
    load_seconds = time.perf_counter() - start
    store = VectorStore(work / config.name, config.model, config.chunk_size, config.chunk_overlap)
    embed_seconds, chunk_tokens, total_chunks = 0.0, [], 0
    for filename, units in corpus.items():
        chunks = chunk_units(units, model.tokenizer, config.chunk_size, config.chunk_overlap, model.max_seq_length)
        start = time.perf_counter()
        vectors = model.encode([c.content for c in chunks], normalize_embeddings=True, batch_size=16).tolist()
        embed_seconds += time.perf_counter() - start
        store.add(filename, chunks, vectors)
        total_chunks += len(chunks)
        chunk_tokens += [len(model.tokenizer(c.content, add_special_tokens=False)['input_ids']) for c in chunks]
    rows, latencies = [], []
    for case in cases:
        start = time.perf_counter()
        query = model.encode([case['question']], normalize_embeddings=True)[0].tolist()
        hits = store.search(query, config.candidates)
        if reranker is not None:
            scores = reranker.predict([(case['question'], hit.content) for hit in hits])
            hits = [hit for _, hit in sorted(zip(scores, hits), key=lambda pair: -pair[0])]
        hits = hits[:config.context_k]
        latencies.append(time.perf_counter() - start)
        rank = next((i for i, hit in enumerate(hits, 1) if source_matches(hit.metadata, case['expected_source'])
                     and contains_keywords(hit.content, case['expected_keywords'])), None)
        rows.append({'id': case['id'], 'dataset': case['dataset'], 'question': case['question'], 'rank': rank,
                     'context_characters': sum(len(hit.content) for hit in hits)})
    del model, reranker
    gc.collect()
    by_dataset = {}
    for dataset in sorted({row['dataset'] for row in rows}):
        ranks = [row['rank'] for row in rows if row['dataset'] == dataset]
        by_dataset[dataset] = {'n': len(ranks), **{f'hit@{k}': round(sum(r is not None and r <= k for r in ranks) / len(ranks), 3)
                                                  for k in (1, 3, config.context_k)},
                               'mrr': round(sum(1 / r for r in ranks if r) / len(ranks), 3),
                               'misses': [row['id'] for row in rows if row['dataset'] == dataset and row['rank'] is None]}
    ranks = [row['rank'] for row in rows]
    return {'config': config.__dict__, 'overall': {'n': len(ranks), 'hit@1': round(sum(r == 1 for r in ranks) / len(ranks), 3),
                                                   'hit@3': round(sum(r is not None and r <= 3 for r in ranks) / len(ranks), 3),
                                                   'hit@context': round(sum(r is not None for r in ranks) / len(ranks), 3),
                                                   'mrr': round(sum(1 / r for r in ranks if r) / len(ranks), 3)},
            'by_dataset': by_dataset, 'model_load_seconds': round(load_seconds, 1),
            'embedding_seconds': round(embed_seconds, 1), 'chunks': total_chunks,
            'chunk_tokens_median': statistics.median(chunk_tokens),
            'query_latency_ms': {'median': round(statistics.median(latencies) * 1000),
                                 'max': round(max(latencies) * 1000)},
            'avg_context_characters': round(statistics.mean(row['context_characters'] for row in rows)),
            'peak_rss_mb_so_far': rss_mb(), 'rows': rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output', type=Path, default=Path('docs/benchmarks/stage2_retrieval_ab.json'))
    parser.add_argument('--extra-cases', type=Path)
    parser.add_argument('--configs', nargs='*', help='subset of config names')
    args = parser.parse_args()
    settings = Settings(_env_file=None)
    cases, documents = load_cases(Path('evaluation'), args.extra_cases)
    for case in cases:
        case['dataset'] = 'extra' if str(case['id']).startswith('extra') else 'evaluation25'
    if BENCHMARK_PDF.is_file():
        documents.append((BENCHMARK_PDF, BENCHMARK_PDF.name))
        cases += [{'id': f'bench-{page}', 'dataset': 'benchmark36p', 'question': question, 'expected_keywords': keys,
                   'expected_source': {'filename': BENCHMARK_PDF.name, 'page': page}}
                  for question, keys, page in BENCHMARK_CASES]
    answerable = [case for case in cases if case['expected_source']]
    corpus = extract(documents, OCRService(settings.ocr_lang), settings)
    results = []
    with tempfile.TemporaryDirectory() as work:
        for config in CONFIGS:
            if args.configs and config.name not in args.configs:
                continue
            result = run_config(config, corpus, answerable, Path(work))
            print(json.dumps({'config': config.name, **result['overall'], 'latency': result['query_latency_ms'],
                              'embed_s': result['embedding_seconds'], 'rss': result['peak_rss_mb_so_far']}), flush=True)
            results.append(result)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({'questions': len(answerable), 'results': results}, ensure_ascii=False, indent=2),
                           encoding='utf-8')
    return 0


if __name__ == '__main__':
    sys.exit(main())
