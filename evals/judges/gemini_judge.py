"""
DeepEval judge backed by Groq.

Environment:
    GROQ_EVAL          -> Groq API key used ONLY for evaluation
    GROQ_EVAL_MODEL    -> optional model override

Default:
    openai/gpt-oss-20b

Important:
    DeepEval can pass a JSON schema to generate().
    We intentionally DO NOT send that schema to Groq's
    json_schema response format because Groq strict schema
    validation can reject DeepEval's schema.

    Instead we use Groq JSON Object Mode and let DeepEval
    validate/parse the returned JSON.

    Groq occasionally returns an empty completion that fails
    its own json_validate_failed check (nothing wrong with our
    request - it's a transient generation glitch). We retry
    that the same way we retry rate limits, and we also strip
    any stray markdown code fences before handing the string
    back to DeepEval.
"""

import asyncio
import os
import time

from dotenv import load_dotenv
from groq import AsyncGroq, Groq, RateLimitError, BadRequestError
from deepeval.models.base_model import DeepEvalBaseLLM

from src.core.config import load_settings


load_dotenv()


DEFAULT_MODEL = "openai/gpt-oss-20b"
MAX_RETRIES = 5

# Groq error codes that are worth retrying rather than failing on -
# these are transient generation glitches, not real request problems.
RETRYABLE_BAD_REQUEST_CODES = {
    "json_validate_failed",
}


def _retry_delay(error: RateLimitError, attempt: int) -> float:
    """
    Use Groq's requested retry delay when available.
    Otherwise use exponential backoff.
    """

    try:
        message = error.response.json()["error"]["message"]

        marker = "try again in"

        if marker in message:
            remaining = (
                message
                .split(marker, 1)[1]
                .strip()
            )

            seconds = remaining.split("s", 1)[0]

            return float(seconds) + 0.5

    except Exception:
        pass

    return min(2 ** attempt, 30)


def _groq_error_code(error: BadRequestError) -> str | None:
    """
    Best-effort extraction of Groq's structured error code so we can
    tell a transient glitch (retry it) from a real bad request
    (surface it).
    """

    try:
        return error.response.json()["error"]["code"]
    except Exception:
        return None


def _strip_code_fences(content: str) -> str:
    """
    DeepEval expects a raw JSON string. Some models wrap their JSON
    output in ```json ... ``` fences even when told not to - strip
    those defensively instead of letting DeepEval's parser choke.
    """

    text = content.strip()

    if text.startswith("```"):

        text = text.split("\n", 1)[-1]

        if text.endswith("```"):
            text = text.rsplit("```", 1)[0]

    return text.strip()


class GeminiJudge(DeepEvalBaseLLM):
    """
    DeepEval-compatible Groq judge.

    The class name remains GeminiJudge so existing evaluation
    code does not need to change.
    """

    def __init__(self, model_name: str | None = None):

        # Evaluation key takes priority.
        api_key = os.getenv("GROQ_EVAL")

        # Fallback to normal Groq configuration only if
        # GROQ_EVAL is not present.
        if not api_key:
            try:
                settings = load_settings()
                api_key = settings.groq.api_key
            except Exception:
                api_key = None

        if not api_key:
            raise RuntimeError(
                "No Groq evaluation API key found. "
                "Set GROQ_EVAL in your .env file."
            )

        self.model_name = (
            model_name
            or os.getenv("GROQ_EVAL_MODEL")
            or DEFAULT_MODEL
        )

        self.client = Groq(api_key=api_key)
        self.async_client = AsyncGroq(api_key=api_key)

    def load_model(self):
        return self.client

    @staticmethod
    def _build_prompt(prompt: str) -> str:
        """
        DeepEval supplies the evaluation instructions.

        Groq JSON Object Mode requires the prompt to explicitly
        request JSON.
        """

        return (
            prompt
            + "\n\n"
            "OUTPUT REQUIREMENTS:\n"
            "Return ONLY a valid JSON object.\n"
            "Follow the output structure requested in the prompt.\n"
            "Do NOT use Markdown code fences.\n"
            "Do NOT include explanations outside the JSON object."
        )

    def generate(self, prompt: str, schema=None) -> str:
        """
        Synchronous generation.

        schema is intentionally accepted because DeepEval may pass
        it through generate_with_schema().

        We intentionally do not send schema to Groq.
        """

        request_prompt = self._build_prompt(prompt)

        for attempt in range(MAX_RETRIES):

            try:

                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=[
                        {
                            "role": "user",
                            "content": request_prompt,
                        }
                    ],
                    temperature=0.0,

                    # IMPORTANT:
                    # Use JSON Object Mode instead of JSON Schema Mode.
                    response_format={
                        "type": "json_object"
                    },

                    # Keep reasoning separate from the JSON output.
                    reasoning_format="hidden",
                )

                if not response.choices:
                    raise RuntimeError(
                        "Groq returned no choices."
                    )

                content = response.choices[0].message.content

                if not content or not content.strip():
                    raise RuntimeError(
                        "Groq returned empty content."
                    )

                return _strip_code_fences(content)

            except RateLimitError as error:

                if attempt == MAX_RETRIES - 1:
                    raise

                delay = _retry_delay(error, attempt)

                print(
                    f"[Groq judge rate limit] "
                    f"retrying in {delay:.1f}s "
                    f"(attempt {attempt + 1}/{MAX_RETRIES})"
                )

                time.sleep(delay)

            except BadRequestError as error:

                code = _groq_error_code(error)

                if (
                    code in RETRYABLE_BAD_REQUEST_CODES
                    and attempt < MAX_RETRIES - 1
                ):

                    delay = min(2 ** attempt, 10)

                    print(
                        f"[Groq judge] retryable bad request "
                        f"({code}), retrying in {delay:.1f}s "
                        f"(attempt {attempt + 1}/{MAX_RETRIES})"
                    )

                    time.sleep(delay)

                    continue

                raise

        raise RuntimeError(
            "Groq judge failed after maximum retries."
        )

    async def a_generate(
        self,
        prompt: str,
        schema=None,
    ) -> str:
        """
        Async generation for DeepEval compatibility.

        Your evaluation currently uses async_mode=False,
        but DeepEval still expects this method to exist.
        """

        request_prompt = self._build_prompt(prompt)

        for attempt in range(MAX_RETRIES):

            try:

                response = await self.async_client.chat.completions.create(
                    model=self.model_name,
                    messages=[
                        {
                            "role": "user",
                            "content": request_prompt,
                        }
                    ],
                    temperature=0.0,

                    response_format={
                        "type": "json_object"
                    },

                    reasoning_format="hidden",
                )

                if not response.choices:
                    raise RuntimeError(
                        "Groq returned no choices."
                    )

                content = response.choices[0].message.content

                if not content or not content.strip():
                    raise RuntimeError(
                        "Groq returned empty content."
                    )

                return _strip_code_fences(content)

            except RateLimitError as error:

                if attempt == MAX_RETRIES - 1:
                    raise

                delay = _retry_delay(error, attempt)

                print(
                    f"[Groq judge rate limit] "
                    f"retrying in {delay:.1f}s "
                    f"(attempt {attempt + 1}/{MAX_RETRIES})"
                )

                await asyncio.sleep(delay)

            except BadRequestError as error:

                code = _groq_error_code(error)

                if (
                    code in RETRYABLE_BAD_REQUEST_CODES
                    and attempt < MAX_RETRIES - 1
                ):

                    delay = min(2 ** attempt, 10)

                    print(
                        f"[Groq judge] retryable bad request "
                        f"({code}), retrying in {delay:.1f}s "
                        f"(attempt {attempt + 1}/{MAX_RETRIES})"
                    )

                    await asyncio.sleep(delay)

                    continue

                raise

        raise RuntimeError(
            "Groq judge failed after maximum retries."
        )

    def get_model_name(self) -> str:
        return self.model_name