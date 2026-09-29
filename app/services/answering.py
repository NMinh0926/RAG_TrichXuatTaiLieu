import logging

from app.models.schemas import AnswerResponse, Source
from app.services.prompts import NO_CONTEXT_ANSWER

logger = logging.getLogger(__name__)


def answer_from_contexts(question, contexts, generation) -> AnswerResponse:
    """No candidates -> fixed answer without the LLM; otherwise the LLM decides support.

    LLM service failures propagate as PipelineError (HTTP 5xx), never as a no-answer.
    """
    if not contexts:
        logger.info('answer outcome=no_candidates')
        return AnswerResponse(answer=NO_CONTEXT_ANSWER)
    verdict = generation.answer(question, contexts)
    if not verdict.supported:
        logger.info('answer outcome=unsupported candidates=%d', len(contexts))
        return AnswerResponse(answer=verdict.answer)
    # Only passages the model cited; the raw Top-K may contain unrelated chunks.
    cited = [contexts[i] for i in dict.fromkeys(verdict.context_indexes) if 0 <= i < len(contexts)]
    if not cited:
        logger.warning('answer supported without a citation matching retrieved metadata; sources omitted')
    logger.info('answer outcome=supported candidates=%d cited=%d', len(contexts), len(cited))
    sources = []
    seen = set()
    for hit in cited:
        meta = hit.metadata
        key = (meta['filename'], meta.get('page'), meta.get('sheet'))
        if key not in seen:
            seen.add(key)
            sources.append(Source(filename=key[0], page=key[1], sheet=key[2]))
    return AnswerResponse(answer=verdict.answer, sources=sources)
