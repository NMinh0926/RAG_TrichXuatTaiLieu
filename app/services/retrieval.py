import logging
from app.models.schemas import SearchHit

logger = logging.getLogger(__name__)


class RetrievalService:
    def __init__(self, settings, embedding, store, reranker=None):
        self.settings, self.embedding, self.store, self.reranker = settings, embedding, store, reranker

    def raw_search(self, question: str, top_k: int | None = None,
                   document_ids: list[str] | None = None) -> list[SearchHit]:
        """Ranked Top-K with no score filtering (diagnostics/evaluation)."""
        if not question.strip():
            raise ValueError('Question must not be blank')
        embedding = self.embedding.embed_query(question.strip())
        return self.store.search(embedding, top_k or self.settings.top_k, document_ids)

    def search_candidates(self, question: str, top_k: int | None = None,
                          document_ids: list[str] | None = None) -> list[SearchHit]:
        """Context chunks for the LLM; never filtered by RETRIEVAL_THRESHOLD.

        With a reranker: RETRIEVAL_CANDIDATES by vector similarity, reordered by the cross-encoder,
        cut to TOP_K. Relevant and no-answer similarities overlap, so the LLM decides support.
        Only CANDIDATE_MIN_RELEVANCE (disabled by default) drops vector noise.
        """
        final_k = top_k or self.settings.top_k
        pool = max(final_k, self.settings.retrieval_candidates) if self.reranker is not None else final_k
        hits = self.raw_search(question, pool, document_ids)
        floor = self.settings.candidate_min_relevance
        candidates = [hit for hit in hits if hit.relevance >= floor]
        if self.reranker is not None and candidates:
            candidates = self.reranker.rerank(question.strip(), candidates)
        candidates = candidates[:final_k]
        logger.info('candidate search raw=%d kept=%d reranked=%s floor=%s top_relevance=%s',
                    len(hits), len(candidates), self.reranker is not None, floor,
                    hits[0].relevance if hits else None)
        return candidates

    def retrieve(self, question: str, top_k: int | None = None) -> list[SearchHit]:
        """Threshold-filtered search, kept for /questions/search and older callers."""
        hits = self.raw_search(question, top_k)
        filtered = [hit for hit in hits if hit.relevance >= self.settings.retrieval_threshold]
        logger.info('retrieval raw_candidates=%d filtered_candidates=%d threshold=%s top_relevance=%s top_distance=%s',
                    len(hits), len(filtered), self.settings.retrieval_threshold,
                    hits[0].relevance if hits else None, hits[0].distance if hits else None)
        return filtered
