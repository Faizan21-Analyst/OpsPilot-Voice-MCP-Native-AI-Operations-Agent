import re

from src.guardrails.base import BaseGuardrail, GuardrailResult


class PIILeakGuard(BaseGuardrail):
    _PATTERNS = {
        "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        "credit_card": re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"),
    }

    @property
    def name(self) -> str:
        return "pii_leak_guard"

    def check(self, text: str, context: dict | None = None) -> GuardrailResult:
        for label, pattern in self._PATTERNS.items():
            if pattern.search(text):
                return GuardrailResult(
                    passed=False,
                    reason=f"pii_leak_detected:{label}",
                    severity="block",
                )
        return GuardrailResult(passed=True)