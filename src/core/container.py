"""
Container: creates and holds all shared objects (the LLM provider, etc).
Built once when the app starts.
"""
from src.core.config import AppSettings
from src.interfaces.llm import BaseLLMProvider
from src.providers.llm.groq_provider import GroqProvider


class Container:
    def __init__(self, settings: AppSettings):
        self.settings = settings
        self.llm: BaseLLMProvider = GroqProvider(settings.groq)

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