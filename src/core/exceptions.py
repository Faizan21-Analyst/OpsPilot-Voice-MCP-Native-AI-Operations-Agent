"""
Custom exceptions for the whole platform.
Every part of our app should raise ONE of these, never a raw Exception.
"""


class PlatformError(Exception):
    """Base class. Every custom error inherits from this."""
    code: str = "platform_error"

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


class ConfigError(PlatformError):
    """Raised when required settings (like an API key) are missing."""
    code = "config_error"


class ProviderError(PlatformError):
    """Base class for anything that goes wrong talking to an LLM provider."""
    code = "provider_error"


class ProviderDownError(ProviderError):
    """The provider (e.g. Groq) is unreachable or returned a server error."""
    code = "provider_down"


class RateLimitError(ProviderError):
    """We got a 429 - too many requests."""
    code = "rate_limited"


class InvalidRequestError(ProviderError):
    """We sent something malformed - bad input, not the provider's fault."""
    code = "invalid_request"