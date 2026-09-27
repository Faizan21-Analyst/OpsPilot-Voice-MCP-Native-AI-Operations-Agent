import numpy as np
from sentence_transformers import CrossEncoder

from src.core.logging import get_logger

logger = get_logger(__name__)


class DocumentReranker:

    def __init__(self, model_name: str = "BAAI/bge-reranker-base"):
        self.model_name = model_name
        self.model = CrossEncoder(model_name)
        logger.info("reranker_model_loaded", model=model_name)

    def rerank(self, query: str, documents: list[dict], top_k: int = 5, min_score: float = 0.55) -> list[dict]:
        if not documents:
            return []

        pairs = [[query, document["content"]] for document in documents]

        # FIX: BAAI/bge-reranker-base (like most sentence-transformers
        # CrossEncoders) outputs raw, unbounded logits from predict() —
        # NOT a 0-1 relevance probability. Comparing those raw logits
        # directly against min_score=0.3 was filtering out genuinely
        # relevant chunks whose raw score happened to be low or negative,
        # since 0.3 only makes sense as a threshold on a probability scale.
        # Applying sigmoid here converts logits -> an actual 0-1 relevance
        # probability, which is what min_score is meant to compare against.
        raw_scores = self.model.predict(pairs)
        scores = 1 / (1 + np.exp(-np.array(raw_scores)))

        reranked = []
        for document, score in zip(documents, scores):
            result = document.copy()
            result["rerank_score"] = float(score)
            reranked.append(result)

        reranked.sort(key=lambda x: x["rerank_score"], reverse=True)

        filtered = [r for r in reranked if r["rerank_score"] >= min_score]

        logger.info(
            "documents_reranked",
            candidates=len(documents),
            above_threshold=len(filtered),
            returned=min(top_k, len(filtered)),
            # Handy for sanity-checking the scale next time something looks off
            top_raw_score=float(raw_scores[0]) if len(raw_scores) else None,
            top_sigmoid_score=float(scores[0]) if len(scores) else None,
        )

        return filtered[:top_k]