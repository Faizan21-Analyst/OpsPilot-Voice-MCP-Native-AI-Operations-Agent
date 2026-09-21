import re

from src.guardrails.base import BaseGuardrail, GuardrailResult

# Heuristic signal words: specific-sounding factual claims that SHOULD
# usually be backed by a docs lookup if this is a policy question.
_CLAIM_INDICATORS = re.compile(
    r"\b(policy|must|requires?|minimum|maximum|\d+\s*(days?|hours?|characters?|attempts?))\b",
    re.IGNORECASE,
)


class GroundingGuard(BaseGuardrail):
    @property
    def name(self) -> str:
        return "grounding_guard"

    def check(self, text: str, context: dict | None = None) -> GuardrailResult:
        tools_called = (context or {}).get("tools_called_this_turn", [])
        docs_was_called = "search_policy_docs" in tools_called

        if not docs_was_called and _CLAIM_INDICATORS.search(text):
            return GuardrailResult(
                passed=True,  # warn only, never blocks the response
                reason="possible_ungrounded_policy_claim",
                severity="warn",
            )
        return GuardrailResult(passed=True)