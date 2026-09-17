from sentence_transformers import SentenceTransformer

from src.core.logging import get_logger


logger = get_logger(__name__)


class DocumentEmbedder:
    def __init__(self,model_name: str = "BAAI/bge-base-en-v1.5",):
        self.model_name = model_name
        self.model = SentenceTransformer(model_name)

        logger.info("embedding_model_loaded",model=model_name,)

    def embed_documents(self,texts: list[str],) -> list[list[float]]:

        if not texts:
            return []

        vectors = self.model.encode(
            texts,
            normalize_embeddings=True,
        )

        logger.info(
            "documents_embedded",
            documents=len(texts),
            dimensions=vectors.shape[1],
        )

        return vectors.tolist()

    def embed_query(self,query: str,) -> list[float]:

        vector = self.model.encode(
            query,
            normalize_embeddings=True,
        )

        return vector.tolist()