"""QA benchmark on the isolated DB built by bench_ingest: retrieval time, time to first token, LLM total.

For each question:
- retrieval: query embedding + Chroma Top-K (same RetrievalService.search_candidates as /ask)
- llm_stream: the exact prompt /ask sends, but streamed (stream=true) to measure time to first token;
  Ollama's own counters give prompt/output tokens and tokens/second
- ask_e2e: the real POST /api/questions/ask (non-streaming, what the frontend waits for)
The first question runs with the model unloaded (cold), the rest warm.
Run: python -m scripts.benchmark.bench_qa
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx

from app.services.ollama_client import OllamaClient

QUESTIONS = [
    {'id': 'fact_text_1', 'question': 'Thời gian thử việc đối với vị trí kỹ sư là bao lâu?', 'expect': '45'},
    {'id': 'fact_text_2', 'question': 'Nhân viên chính thức được nghỉ phép năm bao nhiêu ngày?', 'expect': '16'},
    {'id': 'fact_text_3', 'question': 'Phụ cấp ăn trưa mỗi tháng là bao nhiêu?', 'expect': '850.000'},
    {'id': 'fact_code', 'question': 'Mã quy trình phê duyệt công tác phí là gì?', 'expect': 'CTP-2026-07'},
    {'id': 'fact_ocr_page', 'question': 'Kho vật tư Sao Mai mở cửa lúc mấy giờ?', 'expect': '6 giờ 30'},
    {'id': 'no_answer', 'question': 'Công ty Sao Mai có bao nhiêu nhân viên?', 'expect': None},
    {'id': 'summary', 'question': 'Hãy tóm tắt toàn bộ nội dung của tài liệu quy chế này.', 'expect': None},
]


def stream_chat(client: OllamaClient, system: str, user: str) -> dict:
    body = {'model': client.model, 'stream': True,
            'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
            'options': {'temperature': client.settings.ollama_temperature, 'num_ctx': client.settings.ollama_num_ctx}}
    start = time.perf_counter()
    first = None
    parts, final = [], {}
    with httpx.Client(base_url=client.base_url, timeout=client.settings.ollama_timeout_seconds) as http:
        with http.stream('POST', '/api/chat', json=body) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line:
                    continue
                event = json.loads(line)
                piece = (event.get('message') or {}).get('content', '')
                if piece and first is None:
                    first = time.perf_counter() - start
                parts.append(piece)
                if event.get('done'):
                    final = event
    total = time.perf_counter() - start
    ns = 1e9
    return {
        'ttft_seconds': round(first, 2) if first is not None else None,
        'total_seconds': round(total, 2),
        'load_seconds': round(final.get('load_duration', 0) / ns, 2),
        'prompt_tokens': final.get('prompt_eval_count'),
        'prompt_eval_seconds': round(final.get('prompt_eval_duration', 0) / ns, 2),
        'output_tokens': final.get('eval_count'),
        'output_tokens_per_second': round(final['eval_count'] / (final['eval_duration'] / ns), 1)
        if final.get('eval_duration') else None,
        'answer': ''.join(parts),
    }



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, default=Path('.cache/benchmark/run'))
    parser.add_argument('--output', type=Path, default=Path('docs/benchmarks/baseline_qa.json'))
    args = parser.parse_args()
    from scripts.isolation import isolate, prepare
    isolate(args.work)  # the scratch resources bench_ingest filled
    prepare()
    from fastapi.testclient import TestClient
    from app.core.config import get_settings
    from app.core.dependencies import get_generation, get_services
    from app.core.logging import configure_logging
    from app.main import app
    from app.services.prompts import SYSTEM_PROMPT, build_user_message
    configure_logging()
    get_settings.cache_clear()
    settings = get_settings()
    _, retrieval = get_services()
    total_chunks = retrieval.store.collection.count()
    if not total_chunks:
        parser.exit(1, 'Isolated DB is empty; run scripts.benchmark.bench_ingest first.\n')
    ollama = get_generation().client
    retrieval.search_candidates('khởi động')  # load the embedding model before timing
    # Unload the LLM so the first question measures a cold start.
    httpx.post(f'{ollama.base_url}/api/generate', json={'model': ollama.model, 'keep_alive': 0}, timeout=60)
    rows = []
    with TestClient(app) as client:
        for case in QUESTIONS:
            start = time.perf_counter()
            hits = retrieval.search_candidates(case['question'])
            retrieval_seconds = round(time.perf_counter() - start, 3)
            user = build_user_message(case['question'], hits)
            streamed = stream_chat(ollama, SYSTEM_PROMPT, user)
            start = time.perf_counter()
            response = client.post('/api/questions/ask', json={'question': case['question']})
            ask_seconds = round(time.perf_counter() - start, 2)
            answer = response.json().get('answer', '')
            rows.append({
                **case, 'retrieval_seconds': retrieval_seconds, 'chunks_in_context': len(hits),
                'context_pages': sorted({hit.metadata.get('page') for hit in hits}),
                'context_characters': sum(len(hit.content) for hit in hits),
                'llm_stream': {k: v for k, v in streamed.items() if k != 'answer'},
                'ask_e2e': {'seconds': ask_seconds, 'status': response.status_code,
                            'answer_characters': len(answer), 'answer_words': len(answer.split()),
                            'sources': response.json().get('sources')},
                'answer': answer,
                'expected_found': (case['expect'] in answer) if case['expect'] else None,
            })
            print(f"{case['id']}: retrieval={retrieval_seconds}s ttft={streamed['ttft_seconds']}s "
                  f"llm={streamed['total_seconds']}s ask={ask_seconds}s chars={len(answer)}", flush=True)
    result = {'model': ollama.model, 'top_k': settings.top_k, 'num_ctx': settings.ollama_num_ctx,
              'document_chunks_total': total_chunks, 'questions': rows}
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return 0


if __name__ == '__main__':
    sys.exit(main())
