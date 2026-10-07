import os
import threading

from src.core.config import AppSettings
from src.interfaces.llm import BaseLLMProvider
from src.providers.llm.litellm_provider import LiteLLMProvider
from src.mcp_client.manager import MCPClientManager
from src.permissions.policy_engine import PolicyEngine
from src.agent.graph import build_agent_graph
from dotenv import load_dotenv
load_dotenv()

class Container:
    def __init__(self, settings: AppSettings):
        self.settings = settings

        self.llm: BaseLLMProvider = LiteLLMProvider(settings.groq, settings.gemini, settings.litellm)

        self.manager = MCPClientManager({
            "docs": settings.mcp_servers.docs_url,
            "sql": settings.mcp_servers.sql_url,
            "ops": settings.mcp_servers.ops_url,
        })

        self.policy = PolicyEngine("policies/tools.yaml")

        self.agent = build_agent_graph(self.llm, self.manager, self.policy)

        # The RAG pipeline (embedding model + reranker + Qdrant) is only used by /admin/ingest. Policy SEARCH happens in
        # the docs MCP server, which has its own copy. So it is built on first use instead of at startup: the API starts
        # faster and uses far less memory, and the models are not loaded twice unless someone actually uploads a document.
        self._rag_pipeline = None
        self._rag_lock = threading.Lock()

    def get_rag_pipeline(self):
        """Blocking (loads the models the first time) - call it via asyncio.to_thread from async code."""
        if self._rag_pipeline is None:
            with self._rag_lock:
                if self._rag_pipeline is None:
                    from src.mcp_servers.docs.rag.pipeline import RAGPipeline   # heavy import (torch), so kept lazy

                    self._rag_pipeline = RAGPipeline(
                        llama_parse_api_key=os.getenv("LLAMA_PARSER"),
                        qdrant_url=os.getenv("QDRANT_HTTP"),
                        qdrant_api_key=os.getenv("QDRANT_API_KEY"),
                    )
        return self._rag_pipeline

    @property
    def rag_pipeline(self):
        """Backwards-compatible accessor (same name as before)."""
        return self.get_rag_pipeline()

    async def health_check(self) -> dict:
        llm_health = await self.llm.health_check()
        return {
            "llm": {
                "provider": self.llm.name,
                "healthy": llm_health.healthy,
                "latency_ms": llm_health.latency_ms,
                "error": llm_health.error,
            }
        }
