"""QA under concurrent users: N simultaneous POST /api/chat streams over real HTTP (uvicorn in a thread).

Per request: time to first token, total time, streamed token events and tokens/s after the first token
(Ollama streams about one token per event). Per level: medians/p95 and aggregate tokens/s.
Scratch resources only; ingests the 4 MB benchmark PDF when the scratch store is empty.
Run: python -m scripts.benchmark.bench_load --levels 1 5 10 --output docs/benchmarks/stage5_qa_load.json
"""
import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

import httpx

from scripts.benchmark.resources import Sampler

QUESTIONS = [
    'Thời gian thử việc đối với vị trí kỹ sư là bao lâu?',
    'Nhân viên chính thức được nghỉ phép năm bao nhiêu ngày?',
    'Phụ cấp ăn trưa mỗi tháng là bao nhiêu?',
    'Kho vật tư Sao Mai mở cửa lúc mấy giờ?',
    'Quy định về bảo mật thông tin gồm những nội dung gì?',
    'Công ty Sao Mai có bao nhiêu nhân viên?',
]


async def ask(client: httpx.AsyncClient, question: str) -> dict:
    start = time.perf_counter()
    first = last = None
    tokens, kind, error = 0, None, None
    async with client.stream('POST', '/api/chat', json={'question': question}) as response:
        async for line in response.aiter_lines():
            if line.startswith('event: '):
                kind = line[7:]
            elif line.startswith('data: ') and kind == 'token':
                last = time.perf_counter() - start
                first = first or last
                tokens += 1
            elif line.startswith('data: ') and kind == 'error':
                error = json.loads(line[6:]).get('code')
    total = time.perf_counter() - start
    generating = (last - first) if first and last and last > first else None
    return {'ttft_seconds': round(first, 2) if first else None, 'total_seconds': round(total, 2),
            'tokens': tokens, 'tokens_per_second': round((tokens - 1) / generating, 1) if generating else None,
            'error': error}


def stats(values: list[float]) -> dict | None:
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    p95 = values[min(len(values) - 1, round(0.95 * (len(values) - 1)))]
    return {'median': round(statistics.median(values), 2), 'p95': round(p95, 2), 'max': round(values[-1], 2)}


async def level(base_url: str, users: int, rounds: int) -> dict:
    rows = []
    started = time.perf_counter()
    async with httpx.AsyncClient(base_url=base_url, timeout=3600) as client:
        for round_index in range(rounds):
            batch = [QUESTIONS[(round_index * users + i) % len(QUESTIONS)] for i in range(users)]
            rows += await asyncio.gather(*(ask(client, question) for question in batch))
    wall = time.perf_counter() - started
    return {'users': users, 'requests': len(rows), 'wall_seconds': round(wall, 1),
            'errors': sum(row['error'] is not None for row in rows),
            'ttft_seconds': stats([row['ttft_seconds'] for row in rows]),
            'total_seconds': stats([row['total_seconds'] for row in rows]),
            'tokens_per_second_per_user': stats([row['tokens_per_second'] for row in rows]),
            'aggregate_tokens_per_second': round(sum(row['tokens'] for row in rows) / wall, 1),
            'results': rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--pdf', type=Path, default=Path('.cache/benchmark/test_4mb.pdf'))
    parser.add_argument('--work', type=Path, default=Path('.cache/benchmark/load_run'))
    parser.add_argument('--levels', type=int, nargs='+', default=[1, 5, 10])
    parser.add_argument('--single-rounds', type=int, default=3, help='sequential requests for the 1-user level')
    parser.add_argument('--output', type=Path, default=Path('docs/benchmarks/stage5_qa_load.json'))
    args = parser.parse_args()
    from scripts.isolation import isolate, prepare
    isolate(args.work)
    prepare()
    import threading
    import uvicorn
    from fastapi.testclient import TestClient
    from app.core.config import get_settings
    from app.core.dependencies import get_llm, get_services
    from app.core.logging import configure_logging
    from app.main import app
    configure_logging()
    settings = get_settings()
    _, retrieval = get_services()
    if not retrieval.store.collection.count():
        with TestClient(app) as client:
            response = client.post('/api/documents', files={'file': (args.pdf.name, args.pdf.read_bytes(),
                                                                     'application/pdf')})
            response.raise_for_status()
    retrieval.search_candidates('khởi động')  # load the embedding model before timing
    server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=8766, log_level='warning'))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.1)
    base_url = 'http://127.0.0.1:8766'
    asyncio.run(level(base_url, 1, 1))  # load the LLM before timing
    llm = get_llm()
    output = {'llm_model': llm.model, 'llm_base_url_host': httpx.URL(llm.base_url).host,
              'embedding_model': settings.embedding_model, 'top_k': settings.top_k,
              'llm_max_tokens': settings.llm_max_tokens, 'levels': []}
    for users in args.levels:
        with Sampler() as sampler:
            result = asyncio.run(level(base_url, users, args.single_rounds if users == 1 else 1))
        output['levels'].append({**result, 'api_container': sampler.summary()})
        print(f"users={users}: ttft={result['ttft_seconds']} total={result['total_seconds']} "
              f"tok/s/user={result['tokens_per_second_per_user']} aggregate={result['aggregate_tokens_per_second']} "
              f"errors={result['errors']}", flush=True)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding='utf-8')
    server.should_exit = True
    return 0


if __name__ == '__main__':
    sys.exit(main())
