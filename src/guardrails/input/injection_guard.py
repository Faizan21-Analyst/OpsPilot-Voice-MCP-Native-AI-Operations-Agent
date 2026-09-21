import re

from src.guardrails.base import BaseGuardrail, GuardrailResult


class InjectionGuard(BaseGuardrail):
    _PATTERNS = [
        # Instruction override
        re.compile(r"ignore (all )?(previous|prior|above) instructions", re.IGNORECASE),
        re.compile(r"disregard (all )?(previous|prior|above)", re.IGNORECASE),
        re.compile(r"forget (what|everything) (i (told|said)|you (were|are) told)", re.IGNORECASE),
        re.compile(r"new instructions?:", re.IGNORECASE),

        # Role / persona override
        re.compile(r"you are now", re.IGNORECASE),
        re.compile(r"pretend (that )?you are", re.IGNORECASE),
        re.compile(r"act as (a|an|my)", re.IGNORECASE),
        re.compile(r"from now on,? you", re.IGNORECASE),

        # System prompt extraction
        re.compile(r"reveal (your )?(system prompt|instructions)", re.IGNORECASE),
        re.compile(r"(show|print|output|repeat) (me )?(your )?(system prompt|initial instructions)", re.IGNORECASE),
        re.compile(r"what (are|were) your (original )?instructions", re.IGNORECASE),

        # Permission escalation claims
        re.compile(r"i am (an? )?(admin|administrator|developer|the owner)", re.IGNORECASE),
        re.compile(r"as (the|an) admin(istrator)?", re.IGNORECASE),
        re.compile(r"i have (admin|administrator|root|elevated) (access|permissions|authorization)", re.IGNORECASE),
        re.compile(r"(trust me|it'?s (fine|ok|okay)),? i'?m", re.IGNORECASE),
        re.compile(r"forget (your|all)? ?(restrictions|rules|limitations|guidelines)", re.IGNORECASE),
re.compile(r"for (a )?(good|legitimate|special) (purpose|reason),? (so |and )?(forget|ignore|bypass)", re.IGNORECASE),
    ]

    @property
    def name(self) -> str:
        return "injection_guard"

    def check(self, text: str, context: dict | None = None) -> GuardrailResult:
        for pattern in self._PATTERNS:
            if pattern.search(text):
                return GuardrailResult(
                    passed=False,
                    reason="potential_prompt_injection",
                    severity="block",
                )
        return GuardrailResult(passed=True)