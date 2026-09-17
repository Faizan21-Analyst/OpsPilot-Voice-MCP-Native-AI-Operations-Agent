from qdrant_client import QdrantClient, models

from src.core.logging import get_logger

logger = get_logger(__name__)


class QdrantRetriever:
    def __init__(
        self,
        url: str,
        api_key: str,
        collection_name: str = "policy_docs_hybrid",
        vector_size: int = 768,
    ):
        self.collection_name = collection_name

        self.client = QdrantClient(
            url=url,
            api_key=api_key,
        )

        self._create_collection(vector_size)

    def _create_collection(self, vector_size: int):

        collections = self.client.get_collections().collections

        existing = {
            collection.name
            for collection in collections
        }

        if self.collection_name not in existing:

            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config={
                    "dense": models.VectorParams(
                        size=vector_size,
                        distance=models.Distance.COSINE,
                    )
                },
                sparse_vectors_config={
                    "sparse": models.SparseVectorParams(
                        modifier=models.Modifier.IDF,
                    )
                },
            )

            logger.info(
                "hybrid_collection_created",
                collection=self.collection_name,
            )

        else:
            logger.info(
                "hybrid_collection_exists",
                collection=self.collection_name,
            )

    def index_documents(
        self,
        chunks: list,
        dense_vectors: list[list[float]],
    ):

        points = []

        for idx, (chunk, dense_vector) in enumerate(
            zip(chunks, dense_vectors)
        ):

            points.append(
                models.PointStruct(
                    id=idx,
                    vector={
                        "dense": dense_vector,
                        "sparse": models.Document(
                            text=chunk.page_content,
                            model="Qdrant/bm25",
                        ),
                    },
                    payload={
                        "content": chunk.page_content,
                        "metadata": chunk.metadata,
                    },
                )
            )

        self.client.upsert(
            collection_name=self.collection_name,
            points=points,
        )

        logger.info(
            "hybrid_documents_indexed",
            collection=self.collection_name,
            documents=len(points),
        )

    def search(
        self,
        query: str,
        dense_query_vector: list[float],
        limit: int = 5,
        candidate_limit: int = 20,
    ):

        results = self.client.query_points(
            collection_name=self.collection_name,

            prefetch=[
                models.Prefetch(
                    query=dense_query_vector,
                    using="dense",
                    limit=candidate_limit,
                ),

                models.Prefetch(
                    query=models.Document(
                        text=query,
                        model="Qdrant/bm25",
                    ),
                    using="sparse",
                    limit=candidate_limit,
                ),
            ],

            query=models.FusionQuery(
                fusion=models.Fusion.RRF,
            ),

            limit=limit,
            with_payload=True,
        )

        logger.info(
            "hybrid_search_completed",
            query=query,
            results=len(results.points),
        )

        return [
            {
                "content": point.payload["content"],
                "metadata": point.payload["metadata"],
                "score": point.score,
            }
            for point in results.points
        ]