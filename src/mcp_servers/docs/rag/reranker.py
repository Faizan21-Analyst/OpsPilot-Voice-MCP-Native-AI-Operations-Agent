from sentence_transformers import CrossEncoder

from src.core.logging import get_logger

logger = get_logger(__name__)


class DocumentReranker:

    def __init__(self,model_name: str = "BAAI/bge-reranker-base"):
        self.model_name = model_name

        self.model = CrossEncoder(model_name)

        logger.info("reranker_model_loaded",model=model_name,)

    def rerank(self,query: str,documents: list[dict],top_k: int = 5,) -> list[dict]:

        if not documents:
            return []

        pairs = [
            [query, document["content"]]
            for document in documents
        ]

        scores = self.model.predict(pairs)

        reranked = []

        for document, score in zip(documents, scores):
            result = document.copy()
            result["rerank_score"] = float(score)
            reranked.append(result)

        reranked.sort(
            key=lambda x: x["rerank_score"],
            reverse=True,
        )

        logger.info(
            "documents_reranked",
            candidates=len(documents),
            returned=min(top_k, len(reranked)),
        )

        return reranked[:top_k]