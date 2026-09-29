"""QA benchmark on the scratch resources filled by bench_ingest.

For each question: retrieval time (RetrievalService.search_candidates) and the real streaming endpoint
POST /api/chat over real HTTP (uvicorn in a thread; the test client would buffer the stream) — time to the
first token event, total time, answer length, intent, cited sources.
The summary question is asked twice (second run = cache). With --cold the LLM is unloaded first
(Ollama only: POST <base without /v1>/api/generate keep_alive=0).
Run: python -m scripts.benchmark.bench_qa --output docs/benchmarks/stage2_qa.json [--cold]
"""
import argparse
import json
import sys
import time
from pathlib import Path

import httpx

QUESTIONS = [
    {'id': 'fact_text_1', 'question': 'Thời gian thử việc đối với vị trí kỹ sư là bao lâu?', 'expect': '45'},
    {'id': 'fact_text_2', 'question': 'Nhân viên chính thức được nghỉ phép năm bao nhiêu ngày?', 'expect': '16'},
    {'id': 'fact_text_3', 'question': 'Phụ cấp ăn trưa mỗi tháng là bao nhiêu?', 'expect': '850.000'},
    {'id': 'fact_code', 'question': 'Mã quy trình phê duyệt công tác phí là gì?', 'expect': 'CTP-2026-07'},
    {'id': 'fact_ocr_page', 'question': 'Kho vật tư Sao Mai mở cửa lúc mấy giờ?', 'expect': '6 giờ 30'},
    {'id': 'no_answer', 'question': 'Công ty Sao Mai có bao nhiêu nhân viên?', 'expect': None},
    {'id': 'summary', 'question': 'Hãy tóm tắt toàn bộ nội dung của tài liệu quy chế này.', 'expect': None},
    {'id': 'summary_cached', 'question': 'Tóm tắt tài liệu quy chế.', 'expect': None},
]


def ask(client, question: str) -> dict:
    start = time.perf_counter()
    first, text, done, error, events = None, [], {}, None, 0
    with client.stream('POST', '/api/chat', json={'question': question}) as response:
        kind = None
        for line in response.iter_lines():
            if line.startswith('event: '):
                kind = line[7:]
            elif line.startswith('data: '):
                events += 1
                data = json.loads(line[6:])
                if kind == 'token':
                    first = first or time.perf_counter() - start
                    text.append(data['text'])
                elif kind == 'done':
                    done = data
                elif kind == 'error':
                    error = data
    answer = done.get('answer', ''.join(text))
    return {'ttft_seconds': round(first, 2) if first else None, 'total_seconds': round(time.perf_counter() - start, 2),
            'intent': done.get('intent'), 'answer_characters': len(answer), 'answer_words': len(answer.split()),
            'sources': [{k: s.get(k) for k in ('n', 'filename', 'page')} for s in done.get('sources', [])],
            'error': error, 'answer': answer}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--work', type=Path, default=Path('.cache/benchmark/run'))
    parser.add_argument('--output', type=Path, default=Path('docs/benchmarks/stage2_qa.json'))
    parser.add_argument('--cold', action='store_true', help='unload the Ollama model before the first question')
    parser.add_argument('--only', nargs='*', help='question ids to run (default: all)')
    args = parser.parse_args()
    from scripts.isolation import isolate, prepare
    isolate(args.work)  # the scratch resources bench_ingest filled
    prepare()
    import threading
    import uvicorn
    from app.core.config import get_settings
    from app.core.dependencies import get_llm, get_services
    from app.core.logging import configure_logging
    from app.main import app
    configure_logging()
    settings = get_settings()
    _, retrieval = get_services()
    if not retrieval.store.collection.count():
        parser.exit(1, 'Scratch store is empty; run scripts.benchmark.bench_ingest first.\n')
    llm = get_llm()
    retrieval.search_candidates('khởi động')  # load embedding/reranker models before timing
    if args.cold:
        httpx.post(f"{llm.base_url.removesuffix('/v1')}/api/generate", json={'model': llm.model, 'keep_alive': 0},
                   timeout=60)
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=8765, log_level='warning'))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.1)
    rows = []
    with httpx.Client(base_url='http://127.0.0.1:8765', timeout=3600) as client:
        for case in QUESTIONS:
            if args.only and case['id'] not in args.only:
                continue
            start = time.perf_counter()
            hits = retrieval.search_candidates(case['question'])
            retrieval_seconds = round(time.perf_counter() - start, 3)
            result = ask(client, case['question'])
            rows.append({**case, 'retrieval_seconds': retrieval_seconds,
                         'context_pages': sorted({hit.metadata.get('page') for hit in hits}, key=str),
                         **result, 'expected_found': (case['expect'] in result['answer']) if case['expect'] else None})
            print(f"{case['id']}: retrieval={retrieval_seconds}s ttft={result['ttft_seconds']}s "
                  f"total={result['total_seconds']}s chars={result['answer_characters']} "
                  f"found={rows[-1]['expected_found']} error={result['error']}", flush=True)
    server.should_exit = True
    output = {'llm_model': llm.model, 'summary_model': settings.llm_summary_model or llm.model,
              'embedding_model': settings.embedding_model,
              'reranker_model': settings.reranker_model or None, 'top_k': settings.top_k,
              'retrieval_candidates': settings.retrieval_candidates, 'questions': rows}
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0


if __name__ == '__main__':
    sys.exit(main())
