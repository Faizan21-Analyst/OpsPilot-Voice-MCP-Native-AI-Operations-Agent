import re

from src.guardrails.base import BaseGuardrail, GuardrailResult


class PIIGuard(BaseGuardrail):

    _PATTERNS = {
        "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        "credit_card": re.compile(
            r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"
        ),
        "email": re.compile(
            r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"
        ),
    }

    @property
    def name(self) -> str:
        return "pii_guard"

    def check(self,text: str,context: dict | None = None,) -> GuardrailResult:

        if self._PATTERNS["ssn"].search(text):
            return GuardrailResult(
                passed=False,
                reason="SSN-like personal information detected",
                severity="block",
            )

        if self._PATTERNS["credit_card"].search(text):
            return GuardrailResult(
                passed=False,
                reason="Credit-card-like information detected",
                severity="block",
            )

        if self._PATTERNS["email"].search(text):
            return GuardrailResult(
                passed=True,
                reason="Email address detected",
                severity="warn",
            )

        return GuardrailResult(
            passed=True,
            reason=None,
            severity="block",
        )