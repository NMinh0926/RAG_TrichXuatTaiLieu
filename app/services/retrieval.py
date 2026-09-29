import logging
from app.models.schemas import SearchHit

logger = logging.getLogger(__name__)


class RetrievalService:
    def __init__(self, settings, embedding, store):
        self.settings, self.embedding, self.store = settings, embedding, store

    def raw_search(self, question: str, top_k: int | None = None) -> list[SearchHit]:
        """Ranked Top-K with no score filtering (diagnostics/evaluation)."""
        if not question.strip():
            raise ValueError('Question must not be blank')
        embedding = self.embedding.embed_query(question.strip())
        return self.store.search(embedding, top_k or self.settings.top_k)

    def search_candidates(self, question: str, top_k: int | None = None) -> list[SearchHit]:
        """Ranked Top-K for answer verification; not filtered by RETRIEVAL_THRESHOLD.

        Relevant and no-answer cosine scores overlap, so a fixed similarity cut cannot
        decide support. Only CANDIDATE_MIN_RELEVANCE (disabled by default) drops noise.
        """
        hits = self.raw_search(question, top_k)
        floor = self.settings.candidate_min_relevance
        candidates = [hit for hit in hits if hit.relevance >= floor]
        logger.info('candidate search raw_candidates=%d candidates=%d floor=%s top_relevance=%s top_distance=%s',
                    len(hits), len(candidates), floor,
                    hits[0].relevance if hits else None, hits[0].distance if hits else None)
        return candidates

    def retrieve(self, question: str, top_k: int | None = None) -> list[SearchHit]:
        """Threshold-filtered search, kept for /questions/search and older callers."""
        hits = self.raw_search(question, top_k)
        filtered = [hit for hit in hits if hit.relevance >= self.settings.retrieval_threshold]
        logger.info('retrieval raw_candidates=%d filtered_candidates=%d threshold=%s top_relevance=%s top_distance=%s',
                    len(hits), len(filtered), self.settings.retrieval_threshold,
                    hits[0].relevance if hits else None, hits[0].distance if hits else None)
        return filtered
