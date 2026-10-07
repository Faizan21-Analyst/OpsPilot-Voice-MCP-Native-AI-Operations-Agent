import asyncio
import os

from src.core.logging import get_logger

from src.mcp_servers.docs.rag.parser import DocumentParser
from src.mcp_servers.docs.rag.chunking import DocumentChunker
from src.mcp_servers.docs.rag.embedder import DocumentEmbedder
from src.mcp_servers.docs.rag.retriever import QdrantRetriever
from src.mcp_servers.docs.rag.reranker import DocumentReranker


logger = get_logger(__name__)

# How many chunks to pull from Qdrant and rerank, independent of how many are finally returned (top_k).
# Fetching a few more than we return lets the reranker choose the best ones; lower it (e.g. 3) for speed.
try:
    RERANK_CANDIDATES = max(1, int(os.getenv("OPSPILOT_RAG_CANDIDATES", "5")))
except ValueError:
    RERANK_CANDIDATES = 5


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

        # 1. Embed query  (blocking model call -> worker thread, so the server's event loop stays responsive)
        query_vector = await asyncio.to_thread(
            self.embedder.embed_query,
            query,
        )

        # 2. Hybrid retrieval (fetch at least RERANK_CANDIDATES so the reranker has real choice even when top_k is small)
        candidates = await asyncio.to_thread(
            self.retriever.search,
            query=query,
            dense_query_vector=query_vector,
            limit=max(top_k, RERANK_CANDIDATES),
            candidate_limit=20,
        )

        # 3. Rerank
        results = await asyncio.to_thread(
            self.reranker.rerank,
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