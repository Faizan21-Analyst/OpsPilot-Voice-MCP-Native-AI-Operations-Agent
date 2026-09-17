"""
Concrete implementation of BaseLLMProvider using Groq's API.
"""
import time
from groq import AsyncGroq, APIError, RateLimitError as GroqRateLimitError

from src.interfaces.llm import BaseLLMProvider, LLMResponse, ToolCall, ProviderHealth
from src.core.exceptions import RateLimitError, ProviderDownError
from src.core.config import GroqSettings
from src.core.logging import get_logger

log = get_logger(__name__)


class GroqProvider(BaseLLMProvider):
    def __init__(self, settings: GroqSettings):
        self._settings = settings
        self._client = AsyncGroq(api_key=settings.api_key, timeout=settings.timeout_s)

    @property
    def name(self) -> str:
        return "groq"

    async def generate(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        log.info("groq_generate_start", model=self._settings.model)
        try:
            response = await self._client.chat.completions.create(
                model=self._settings.model,
                messages=messages,
                tools=tools,
            )
        except GroqRateLimitError as e:
            log.warning("groq_rate_limited", error=str(e))
            raise RateLimitError(f"Groq rate limit hit: {e}") from e
        except APIError as e:
            log.error("groq_api_error", error=str(e))
            raise ProviderDownError(f"Groq API error: {e}") from e

        choice = response.choices[0]
        raw_tool_calls = choice.message.tool_calls or []

        tool_calls = [
            ToolCall(id=tc.id, name=tc.function.name, arguments=tc.function.arguments)
            for tc in raw_tool_calls
        ]

        return LLMResponse(
            content=choice.message.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason,
            usage={
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
            },
            provider=self.name,
            model=self._settings.model,
        )

    async def health_check(self) -> ProviderHealth:
        start = time.perf_counter()
        try:
            await self.generate(messages=[{"role": "user", "content": "ping"}])
            latency_ms = (time.perf_counter() - start) * 1000
            return ProviderHealth(healthy=True, latency_ms=latency_ms)
        except Exception as e:
            return ProviderHealth(healthy=False, error=str(e))