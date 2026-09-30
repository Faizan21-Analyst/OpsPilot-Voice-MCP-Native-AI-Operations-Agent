from src.core.config import AppSettings
from src.interfaces.llm import BaseLLMProvider
from src.providers.llm.litellm_provider import LiteLLMProvider
from src.mcp_client.manager import MCPClientManager
from src.permissions.policy_engine import PolicyEngine
from src.agent.graph import build_agent_graph
from src.mcp_servers.docs.rag.pipeline import RAGPipeline
import os 
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

        self.rag_pipeline = RAGPipeline(
                    llama_parse_api_key=os.getenv("LLAMA_PARSER"),   # check your actual settings field names
                    qdrant_url=os.getenv("QDRANT_HTTP"),
                    qdrant_api_key=os.getenv("QDRANT_API_KEY"),
                )

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