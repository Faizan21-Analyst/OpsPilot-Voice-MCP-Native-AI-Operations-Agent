import pytest
from unittest.mock import AsyncMock, patch
from groq import RateLimitError as GroqRateLimitError

from src.providers.llm.groq_provider import GroqProvider
from src.core.config import GroqSettings
from src.core.exceptions import RateLimitError


@pytest.mark.asyncio
async def test_rate_limit_translates_to_our_exception():
    settings = GroqSettings(api_key="fake_key")
    provider = GroqProvider(settings)

    with patch.object(
        provider._client.chat.completions, "create",
        new=AsyncMock(side_effect=GroqRateLimitError("rate limited", response=None, body=None)),
    ):
        with pytest.raises(RateLimitError):
            await provider.generate(messages=[{"role": "user", "content": "hi"}])