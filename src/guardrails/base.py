from abc import ABC , abstractmethod
from dataclasses import dataclass

@dataclass
class GuardrailResult:
    passed: bool
    reason: str | None = None
    severity: str = "block" 

class BaseGuardrail(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def check(self, text: str, context: dict | None = None) -> GuardrailResult: ...