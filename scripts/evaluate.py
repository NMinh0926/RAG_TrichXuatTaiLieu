"""Real retrieval and optional real LLM evaluation. Never substitutes a fake LLM.

Ranking is measured on the raw Top-K (before any threshold); answering uses the same
candidate -> LLM verification flow as /api/questions/ask.
"""
import argparse
import json
import re
import statistics
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.core.config import Settings
from app.core.errors import PipelineError
from app.core.logging import configure_logging
from app.services.embedding import EmbeddingService
from app.services.generation import GenerationService
from app.llm.prompts import INSUFFICIENT_ANSWER, NO_CONTEXT_ANSWER
from app.services.ingestion import IngestionService
from app.services.ocr_service import OCRService
from app.services.retrieval import RetrievalService
from app.services.vector_store import VectorStore
from app.services.answering import answer_from_contexts


NO_ANSWERS = (NO_CONTEXT_ANSWER, INSUFFICIENT_ANSWER)


def contains_keywords(text, keywords):
    # Normalize digit group separators, without letting 12 match 120.
    text = re.sub(r'(?<=\d)[.,](?=\d{3}(?:\D|$))', '', text.casefold())
    return all(re.search(r'(?<!\w)' + re.escape(word.casefold()) + r'(?!\w)', text)
               for word in keywords)


def source_matches(metadata, source):
    return source is not None and all(metadata.get(key) == value for key, value in source.items())


def relevant_rank(hits, case):
    """1-based rank of the first chunk from the expected source containing the expected facts."""
    return next((rank for rank, hit in enumerate(hits, 1) if source_matches(hit.metadata, case['expected_source'])
                 and contains_keywords(hit.content, case['expected_keywords'])), None)


def distribution(values):
    if not values:
        return None
    return {'n': len(values), 'min': round(min(values), 6), 'median': round(statistics.median(values), 6),
            'max': round(max(values), 6)}


def ranking_metrics(rows, top_k, threshold):
    answerable = [row for row in rows if row['expected_source'] and 'raw_top_k' in row]
    unanswerable = [row for row in rows if not row['expected_source'] and 'raw_top_k' in row]
    ranks = [row['relevant_rank'] for row in answerable]
    hit_at = {f'hit@{k}': sum(r is not None and r <= k for r in ranks) / len(ranks) if ranks else None
              for k in sorted({1, 3, top_k})}
    relevant_scores = [row['relevant_relevance'] for row in answerable if row['relevant_relevance'] is not None]
    no_answer_top1 = [row['raw_top_k'][0]['relevance'] for row in unanswerable if row['raw_top_k']]
    answerable_top1 = [row['raw_top_k'][0]['relevance'] for row in answerable if row['raw_top_k']]
    overlap = bool(relevant_scores and no_answer_top1 and min(relevant_scores) <= max(no_answer_top1))
    # What a candidate floor would cost: relevant chunks lost vs. no-answer queries left with no candidates.
    floors = []
    for floor in [round(i * 0.05, 2) for i in range(13)]:
        floors.append({'floor': floor,
                       'relevant_kept': sum(s >= floor for s in relevant_scores),
                       'no_answer_emptied': sum(s < floor for s in no_answer_top1)})
    return {
        'answerable': len(answerable), 'no_answer': len(unanswerable), 'top_k': top_k, **hit_at,
        'mrr': sum(1 / r for r in ranks if r) / len(ranks) if ranks else None,
        'misses': [row['id'] for row in answerable if row['relevant_rank'] is None],
        'relevant_chunk_relevance': distribution(relevant_scores),
        'answerable_top1_relevance': distribution(answerable_top1),
        'no_answer_top1_relevance': distribution(no_answer_top1),
        'relevant_vs_no_answer_overlap': overlap,
        'legacy_threshold': threshold,
        'legacy_threshold_hit_rate': (sum(r is not None and row['relevant_relevance'] >= threshold
                                          for r, row in zip(ranks, answerable)) / len(ranks)) if ranks else None,
        'candidate_floor_sweep': floors,
    }


def load_cases(root: Path, extra: Path | None):
    """Evaluation dataset plus optional local cases: {"documents": [{"path", "filename"}], "cases": [...]}."""
    cases = json.loads((root / 'questions.json').read_text(encoding='utf-8'))
    documents = [(root / 'documents' / name, name)
                 for name in sorted({case['expected_source']['filename'] for case in cases if case['expected_source']})]
    if extra:
        data = json.loads(extra.read_text(encoding='utf-8'))
        documents += [(Path(doc['path']), doc.get('filename') or Path(doc['path']).name) for doc in data['documents']]
        cases += [{**case, 'id': f"extra-{case['id']}"} for case in data['cases']]
    return cases, documents


def evaluate(root: Path, output: Path, retrieval_only=False, extra: Path | None = None):
    settings = Settings()
    generation = GenerationService(settings)
    if not retrieval_only:
        generation.client.health()  # fail before downloads/ingestion if LLM/model is missing
    cases, documents = load_cases(root, extra)
    # Separate database for every run, never touches the application's uploaded documents.
    store = VectorStore(Path('.cache/evaluation') / uuid4().hex, settings.embedding_model,
                        settings.chunk_size, settings.chunk_overlap)
    embedding = EmbeddingService(settings.embedding_model)
    ingestion = IngestionService(settings, OCRService(settings.ocr_lang), embedding, store)
    retrieval = RetrievalService(settings, embedding, store)
    for path, filename in documents:
        ingestion.ingest(path, filename)
    rows = []
    for case in cases:
        row = {'id': case['id'], 'question': case['question'], 'category': case.get('category'),
               'expected_source': case['expected_source'], 'relevant_rank': None, 'relevant_relevance': None,
               'retrieval_hit': None, 'answer_correct': None, 'no_answer_correct': None}
        try:
            # Ranking uses the unfiltered Top-K; the candidate floor applies only to answering, as in /ask.
            hits = retrieval.raw_search(case['question'])
            row['raw_top_k'] = [{'rank': rank, 'metadata': hit.metadata, 'relevance': round(hit.relevance, 6),
                                 'preview': hit.content[:80]} for rank, hit in enumerate(hits, 1)]
            if case['expected_source']:
                row['relevant_rank'] = relevant_rank(hits, case)
                if row['relevant_rank']:
                    row['relevant_relevance'] = round(hits[row['relevant_rank'] - 1].relevance, 6)
                # Legacy metric: relevant chunk survives RETRIEVAL_THRESHOLD.
                row['retrieval_hit'] = any(hit.relevance >= settings.retrieval_threshold and
                                           source_matches(hit.metadata, case['expected_source']) and
                                           contains_keywords(hit.content, case['expected_keywords'])
                                           for hit in hits)
            if not retrieval_only:
                candidates = [hit for hit in hits if hit.relevance >= settings.candidate_min_relevance]
                response = answer_from_contexts(case['question'], candidates, generation)
                row['answer'] = response.answer
                row['sources'] = [source.model_dump(exclude_none=True) for source in response.sources]
                if case['expected_source']:
                    row['answer_correct'] = response.answer not in NO_ANSWERS and contains_keywords(
                        response.answer, case['expected_keywords']) and any(
                            source_matches(source, case['expected_source']) for source in row['sources'])
                else:
                    row['no_answer_correct'] = response.answer in NO_ANSWERS and not response.sources
                    row['answer_correct'] = row['no_answer_correct']
        except PipelineError as exc:
            row['error'] = exc.code
            if case['expected_source'] and row['retrieval_hit'] is None:
                row['retrieval_hit'] = False
            if not retrieval_only:
                row['answer_correct'] = False
                if case['expected_source'] is None:
                    row['no_answer_correct'] = False
        rows.append(row)
    answerable = sum(case['expected_source'] is not None for case in cases)
    no_answer_total = len(cases) - answerable
    hits = sum(row['retrieval_hit'] is True for row in rows)
    correct = sum(row['answer_correct'] is True for row in rows) if not retrieval_only else None
    answerable_correct = (sum(row['answer_correct'] is True for row in rows if row['expected_source'])
                          if not retrieval_only else None)
    abstentions = sum(row['no_answer_correct'] is True for row in rows) if not retrieval_only else None
    errors = [row['error'] for row in rows if 'error' in row]
    result = {
        'mode': 'retrieval_only' if retrieval_only else 'real_llm',
        'created_at': datetime.now(timezone.utc).isoformat(),
        'embedding_model': settings.embedding_model, 'llm_model': settings.llm_model if not retrieval_only else None,
        'top_k': settings.top_k, 'threshold': settings.retrieval_threshold,
        'candidate_min_relevance': settings.candidate_min_relevance,
        'retrieval_ranking': ranking_metrics(rows, settings.top_k, settings.retrieval_threshold),
        'answering': None if retrieval_only else {
            'answer_accuracy_answerable': answerable_correct / answerable if answerable else None,
            'unsupported_accuracy_no_answer': abstentions / no_answer_total if no_answer_total else None,
            'error_codes': sorted(set(errors)),
        },
        'metrics': {'total_questions': len(cases), 'answerable_questions': answerable,
                    # Legacy: relevant chunk must pass RETRIEVAL_THRESHOLD. Not what /ask uses now.
                    'retrieval_hit_count': hits, 'retrieval_hit_rate': hits / answerable if answerable else None,
                    'correct_answer_count': correct, 'answer_accuracy': correct / len(cases) if correct is not None else None,
                    'no_answer_total': no_answer_total, 'no_answer_correct_count': abstentions,
                    'no_answer_accuracy': abstentions / no_answer_total if abstentions is not None and no_answer_total else None,
                    'error_count': len(errors)},
        'cases': rows,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--retrieval-only', action='store_true', help='Do not call LLM; answer metrics remain null')
    parser.add_argument('--dataset', type=Path, default=Path('evaluation'))
    parser.add_argument('--extra-cases', type=Path, default=None,
                        help='Optional local JSON with extra documents/cases (e.g. a user-approved PDF)')
    parser.add_argument('--output', type=Path, default=None)
    args = parser.parse_args()
    output = args.output or Path('evaluation') / ('results_retrieval.json' if args.retrieval_only else 'results_llm.json')
    configure_logging()
    try:
        result = evaluate(args.dataset, output, args.retrieval_only, args.extra_cases)
    except PipelineError as exc:
        parser.exit(1, f'{exc.code}: {exc}\n')
    summary = {key: value for key, value in result['retrieval_ranking'].items() if key != 'candidate_floor_sweep'}
    print(json.dumps({'retrieval_ranking': summary, 'answering': result['answering'],
                      'metrics': result['metrics']}, ensure_ascii=False, indent=2))
    print(f'Results: {output}')
    return 1 if result['metrics']['error_count'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
