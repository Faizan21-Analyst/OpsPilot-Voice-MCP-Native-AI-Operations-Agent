import sys
import os
import asyncio
import time
from pathlib import Path
from dotenv import load_dotenv

# Robust path resolution
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if os.getcwd() not in sys.path:
    sys.path.insert(0, os.getcwd()) 

from fastmcp import FastMCP
from src.core.config import load_settings
from src.core.logging import get_logger, configure_logging

load_dotenv()
logger = get_logger(__name__)
mcp = FastMCP("docs-server")

settings = load_settings()
# WARNING: Ensure this logging config writes to STDERR, not STDOUT!
configure_logging(settings.log_level)

_rag_pipeline = None


def get_pipeline():
    """Return the shared RAG pipeline. Normally it already exists (built at startup, see preload_pipeline below);
    if the startup load failed (for example Qdrant was unreachable) we retry here on first use."""
    global _rag_pipeline
    if _rag_pipeline is None:
        logger.info("Loading RAG pipeline models (embedder + reranker + Qdrant connection)...")

        # Heavy import (torch / transformers / sentence-transformers) lives here, not at the top of the file.
        from src.mcp_servers.docs.rag.pipeline import RAGPipeline

        parse_api_key = os.getenv('LLAMA_PARSER')
        qdrant_url = os.getenv("QDRANT_HTTP")
        qdrant_api_key = os.getenv('QDRANT_API_KEY')

        _rag_pipeline = RAGPipeline(
            llama_parse_api_key=parse_api_key,
            qdrant_url=qdrant_url,
            qdrant_api_key=qdrant_api_key,
        )
    return _rag_pipeline


def preload_pipeline() -> None:
    """Load the embedding model, reranker and Qdrant connection NOW (server start), not on the first user question.
    Then run one throw-away search so the first real query does not pay for lazy kernel / tokenizer initialisation."""
    started = time.perf_counter()
    try:
        pipeline = get_pipeline()
        loaded = time.perf_counter() - started
        try:
            asyncio.run(pipeline.search(query="warm up", top_k=1))
        except Exception as e:  # warm-up is best effort: a failure here must never stop the server from starting
            logger.warning("rag_warmup_search_failed", error=str(e))
        logger.info(
            "rag_pipeline_preloaded",
            load_seconds=round(loaded, 1),
            total_seconds=round(time.perf_counter() - started, 1),
        )
    except Exception as e:
        # Server still starts; search_policy_docs retries the load on first use (and returns [] if that fails too).
        logger.error("rag_preload_failed", error=str(e))


# Runs once, when the docs server process starts and BEFORE it begins serving requests.
preload_pipeline()


# Keep tool output small: it is pasted into the next LLM prompt, and Groq limits input tokens per minute.
def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


MAX_RESULTS = _env_int("OPSPILOT_RAG_MAX_RESULTS", 3)        # chunks returned to the model
MAX_CHUNK_CHARS = _env_int("OPSPILOT_RAG_CHUNK_CHARS", 1000)  # per chunk (chunks are <=1200 chars when indexed)


@mcp.tool()
async def search_policy_docs(query: str, top_k: int = 3) -> list[dict]:
    try:
        pipeline = get_pipeline()
        results = await pipeline.search(query=query, top_k=max(1, min(top_k, MAX_RESULTS)))
        
        return [
            {
                "content": r["content"][:MAX_CHUNK_CHARS],
                "source": Path(str(r.get("metadata", {}).get("source", "unknown"))).name,
                "score": round(float(r.get("rerank_score", r.get("score", 0.0))), 3),
            }
            for r in results
        ]
    except Exception as e:
        logger.error("search_policy_docs_failed", query=query, error=str(e))
        return []
    
if __name__ == "__main__":
    mcp.run(transport="streamable-http", host=os.getenv("MCP_HOST", "127.0.0.1"), port=8001)
