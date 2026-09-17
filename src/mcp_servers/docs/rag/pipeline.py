from src.core.logging import get_logger

from src.mcp_servers.docs.rag.parser import DocumentParser
from src.mcp_servers.docs.rag.chunking import DocumentChunker
from src.mcp_servers.docs.rag.embedder import DocumentEmbedder
from src.mcp_servers.docs.rag.retriever import QdrantRetriever
from src.mcp_servers.docs.rag.reranker import DocumentReranker


logger = get_logger(__name__)


class RAGPipeline:

    def __init__(
        self,
        llama_parse_api_key: str,
        qdrant_url: str,
        qdrant_api_key: str,):

        self.parser = DocumentParser(
            api_key=llama_parse_api_key
        )

        self.chunker = DocumentChunker()

        self.embedder = DocumentEmbedder()

        self.retriever = QdrantRetriever(
            url=qdrant_url,
            api_key=qdrant_api_key,
        )

        self.reranker = DocumentReranker()

        logger.info("rag_pipeline_initialized")

    async def index_document(
        self,
        file_path: str,
    ):

        logger.info(
            "rag_indexing_started",
            file=file_path,
        )

        # 1. Parse
        document = await self.parser.parse(
            file_path
        )

        # 2. Chunk
        chunks = self.chunker.chunk(
            document=document,
            source=file_path,
        )

        # 3. Embed
        texts = [
            chunk.page_content
            for chunk in chunks
        ]

        vectors = self.embedder.embed_documents(
            texts
        )

        # 4. Store
        self.retriever.index_documents(
            chunks=chunks,
            dense_vectors=vectors,
        )

        logger.info(
            "rag_indexing_completed",
            file=file_path,
            chunks=len(chunks),
        )

        return {
            "source": file_path,
            "chunks": len(chunks),
        }

    async def search(
        self,
        query: str,
        top_k: int = 5,
    ):

        logger.info(
            "rag_search_started",
            query=query,
        )

        # 1. Embed query
        query_vector = self.embedder.embed_query(
            query
        )

        # 2. Hybrid retrieval
        candidates = self.retriever.search(
            query=query,
            dense_query_vector=query_vector,
            limit=top_k,
            candidate_limit=20,
        )

        # 3. Rerank
        results = self.reranker.rerank(
            query=query,
            documents=candidates,
            top_k=top_k,
        )

        logger.info(
            "rag_search_completed",
            query=query,
            candidates=len(candidates),
            results=len(results),
        )

        return results