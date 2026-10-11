import time
import litellm
from litellm.router import Router, RetryPolicy
from litellm.exceptions import (
    RateLimitError as LiteLLMRateLimitError,
    APIError as LiteLLMAPIError,
    BadRequestError as LiteLLMBadRequestError,
)

from src.interfaces.llm import BaseLLMProvider, LLMResponse, ToolCall, ProviderHealth
from src.core.exceptions import RateLimitError, ProviderDownError
from src.core.config import GroqSettings, GeminiSettings, LiteLLMSettings
from src.core.logging import get_logger

log = get_logger(__name__)

# Groq occasionally emits a malformed <tool_call> block instead of a
# clean function call (litellm surfaces this as a BadRequestError with
# code "tool_use_failed"). It's a transient generation glitch, not a
# real bad request, so we retry it a couple of times before giving up.
MALFORMED_TOOL_CALL_RETRIES = 2


class LiteLLMProvider(BaseLLMProvider):
    def __init__(self, groq: GroqSettings, gemini: GeminiSettings, litellm_settings: LiteLLMSettings):
        keys = groq.api_keys

        def groq_deployments(group: str, model: str) -> list[dict]:
            """One deployment per Groq key under the same group name: the router spreads requests over them and skips
            a key while it is rate limited. The output cap is set per deployment so it never reaches Gemini."""
            params_extra = {"max_tokens": litellm_settings.max_output_tokens} if litellm_settings.max_output_tokens > 0 else {}
            return [
                {
                    "model_name": group,
                    "litellm_params": {"model": f"groq/{model}", "api_key": key, **params_extra},
                    "model_info": {"id": f"{group}-key-{i}"},
                }
                for i, key in enumerate(keys, start=1)
            ]

        model_list = groq_deployments("primary-groq", groq.model)
        # Order tried after every Groq key (main model) has failed: another Groq model, then Gemini, then a second Gemini.
        fallback_groups: list[str] = []
        if groq.fallback_model:
            model_list += groq_deployments("backup-groq", groq.fallback_model)
            fallback_groups.append("backup-groq")
        model_list.append({"model_name": "primary-gemini", "litellm_params": {"model": gemini.model, "api_key": gemini.api_key}})
        fallback_groups.append("primary-gemini")
        if gemini.fallback_model:
            model_list.append({"model_name": "backup-gemini", "litellm_params": {"model": gemini.fallback_model, "api_key": gemini.api_key}})
            fallback_groups.append("backup-gemini")

        self._router = Router(
            model_list=model_list,
            fallbacks=[{"primary-groq": fallback_groups}],
            num_retries=litellm_settings.num_retries,
            # A 429 will not succeed if repeated on the SAME key, but another key (another organisation) has its own
            # limit, so a rate-limited request is retried on the other keys. Once all are cooling down, the router
            # moves on to the fallback models.
            retry_policy=RetryPolicy(RateLimitErrorRetries=max(0, len(keys) - 1)),
            cooldown_time=litellm_settings.cooldown_s,
            timeout=litellm_settings.timeout_s,
        )
        log.info(
            "llm_router_ready",
            groq_keys=len(keys),
            groq_model=groq.model,
            fallbacks=fallback_groups,
            max_output_tokens=litellm_settings.max_output_tokens,
        )

    @property
    def name(self) -> str:
        return "litellm"

    async def generate(self, messages, tools=None) -> LLMResponse:
        response = None
        last_error = None

        for attempt in range(MALFORMED_TOOL_CALL_RETRIES + 1):

            try:
                response = await self._router.acompletion(
                    model="primary-groq",   # entry point; router falls to primary-gemini on failure
                    messages=messages,
                    tools=tools,
                )
                break

            except LiteLLMRateLimitError as e:
                raise RateLimitError(f"LiteLLM rate limit: {e}") from e

            except LiteLLMBadRequestError as e:
                if (
                    "tool_use_failed" in str(e)
                    and attempt < MALFORMED_TOOL_CALL_RETRIES
                ):
                    last_error = e
                    log.warning(
                        "malformed_tool_call_retry",
                        attempt=attempt + 1,
                        error=str(e)[:300],
                    )
                    continue
                raise ProviderDownError(f"LiteLLM provider error: {e}") from e

            except LiteLLMAPIError as e:
                raise ProviderDownError(f"LiteLLM provider error: {e}") from e

        if response is None:
            raise ProviderDownError(
                f"LiteLLM provider error after "
                f"{MALFORMED_TOOL_CALL_RETRIES} retries: {last_error}"
            )

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