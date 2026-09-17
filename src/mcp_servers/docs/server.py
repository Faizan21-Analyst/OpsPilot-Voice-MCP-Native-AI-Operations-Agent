import sys
import os
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
    global _rag_pipeline
    if _rag_pipeline is None:
        logger.info("Initializing RAG Pipeline (Lazy Load)...")
        
        # 🔥 HEAVY IMPORT MOVED HERE 🔥
        # This prevents torch/transformers from loading until you actually search
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

@mcp.tool()
async def search_policy_docs(query: str, top_k: int = 5) -> list[dict]:
    try:
        pipeline = get_pipeline()
        results = await pipeline.search(query=query, top_k=top_k)
        
        return [
            {
                "content": r["content"],
                "source": r.get("metadata", {}).get("source", "unknown"),
                "score": r.get("rerank_score", r.get("score", 0.0)),
            }
            for r in results
        ]
    except Exception as e:
        logger.error("search_policy_docs_failed", query=query, error=str(e))
        return []
    
if __name__ == "__main__":
    mcp.run()