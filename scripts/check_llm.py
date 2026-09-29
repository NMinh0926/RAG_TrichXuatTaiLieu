"""Check the LLM setup with SYNTHETIC text only (no user documents).

1. GET {LLM_BASE_URL}/models and confirm LLM_MODEL is available.
2. Grounded answer: the synthetic context contains the code -> answer contains it and cites [1].
3. Missing fact: the context does not contain it -> the insufficient-context sentence.
Run: python -m scripts.check_llm
"""
import json
import sys

from app.core.config import Settings
from app.core.errors import PipelineError
from app.core.logging import configure_logging
from app.models.schemas import SearchHit
from app.services.generation import GenerationService

CONTEXT = SearchHit(id='synthetic-1', content='Dự án Orion có mã nội bộ ZXQ-91827.',
                    metadata={'filename': 'synthetic.pdf', 'page': 1}, distance=0.0, relevance=1.0)
EXPECTED = 'ZXQ-91827'
CASES = [('Mã nội bộ của dự án Orion là gì?', True), ('Giám đốc điều hành của dự án Orion là ai?', False)]


def check(settings: Settings) -> dict:
    service = GenerationService(settings)
    result = {'base_url': service.client.base_url, 'model': service.client.model, 'health': None, 'grounded_tests': []}
    try:
        result['health'] = service.client.health()
    except PipelineError as exc:
        result['health'] = {'status': 'error', 'error': exc.code, 'detail': str(exc)}
        return result
    for question, supported in CASES:
        try:
            verdict = service.answer(question, [CONTEXT])
            passed = verdict.supported is supported and (not supported or (EXPECTED in verdict.answer
                                                                            and verdict.context_indexes == [0]))
            result['grounded_tests'].append({'question': question, 'expected_supported': supported,
                                             'supported': verdict.supported, 'answer': verdict.answer, 'pass': passed})
        except PipelineError as exc:
            result['grounded_tests'].append({'question': question, 'error': exc.code, 'detail': str(exc), 'pass': False})
    return result


def main() -> int:
    configure_logging()
    result = check(Settings())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    healthy = result['health'].get('status') == 'ok'
    grounded = bool(result['grounded_tests']) and all(row['pass'] for row in result['grounded_tests'])
    print('LLM HEALTH PASS' if healthy else f"LLM HEALTH FAIL: {result['health'].get('detail')}")
    if healthy:
        print('LLM GROUNDED BEHAVIOR PASS' if grounded else 'LLM GROUNDED BEHAVIOR FAIL')
    return 0 if healthy and grounded else 1


if __name__ == '__main__':
    sys.exit(main())
