"""
BaseLLMProvider: the contract every LLM provider must follow.
Think of this as a rulebook. Groq, Gemini, etc. will each write their
own class that obeys these rules.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ToolCall:
    """Represents one function/tool the LLM wants to call."""
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    """Standard response shape - same regardless of which provider answered."""
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: dict = field(default_factory=dict)   # e.g. {"prompt_tokens": 10, "completion_tokens": 20}
    provider: str = ""
    model: str = ""


@dataclass
class ProviderHealth:
    """Result of a health check."""
    healthy: bool
    latency_ms: float | None = None
    error: str | None = None


class BaseLLMProvider(ABC):
    """
    Abstract base class. You CANNOT create an instance of this directly.
    Any subclass (like GroqProvider) MUST implement every @abstractmethod
    below, or Python will refuse to let you create it.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier, e.g. 'groq'."""
        ...

    @abstractmethod
    async def generate(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        """Send messages to the LLM, get one complete response back."""
        ...

    @abstractmethod
    async def health_check(self) -> ProviderHealth:
        """Quick check: is this provider working right now?"""
        ...