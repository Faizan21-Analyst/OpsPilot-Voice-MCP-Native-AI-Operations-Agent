import time
import litellm
from litellm.router import Router
from litellm.exceptions import RateLimitError as LiteLLMRateLimitError, APIError as LiteLLMAPIError

from src.interfaces.llm import BaseLLMProvider, LLMResponse, ToolCall, ProviderHealth
from src.core.exceptions import RateLimitError, ProviderDownError
from src.core.config import GroqSettings, GeminiSettings, LiteLLMSettings
from src.core.logging import get_logger

log = get_logger(__name__)


class LiteLLMProvider(BaseLLMProvider):
    def __init__(self, groq: GroqSettings, gemini: GeminiSettings, litellm_settings: LiteLLMSettings):
        self._router = Router(
            model_list=[
                {
                    "model_name": "primary",
                    "litellm_params": {
                        "model": f"groq/{groq.model}",
                        "api_key": groq.api_key,
                    },
                },
                {
                    "model_name": "secondary",
                    "litellm_params": {
                        "model": gemini.model,
                        "api_key": gemini.api_key,
                    },
                },
            ],
            num_retries=litellm_settings.num_retries,
            timeout=litellm_settings.timeout_s,
        )

    @property
    def name(self) -> str:
        return "litellm"

    async def generate(self, messages, tools=None) -> LLMResponse:
        try:
            response = await self._router.acompletion(
                model="primary",
                messages=messages,
                tools=tools,
            )
        except LiteLLMRateLimitError as e:
            raise RateLimitError(f"LiteLLM rate limit: {e}") from e
        except LiteLLMAPIError as e:
            raise ProviderDownError(f"LiteLLM provider error: {e}") from e

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
            provider=response.get("model", "unknown"),  
            model=response.get("model", "unknown"),
        )

    async def health_check(self) -> ProviderHealth:
        start = time.perf_counter()
        try:
            await self.generate(messages=[{"role": "user", "content": "ping"}])
            return ProviderHealth(healthy=True, latency_ms=(time.perf_counter() - start) * 1000)
        except Exception as e:
            return ProviderHealth(healthy=False, error=str(e))