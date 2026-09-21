from src.agent.state import AgentState
from src.guardrails.input.pii_guard import PIIGuard
from src.guardrails.input.injection_guard import InjectionGuard
from src.core.logging import get_logger

logger = get_logger(__name__)


def build_input_guard_node(pii_guard: PIIGuard, injection_guard: InjectionGuard):

    async def input_guard_node(state: AgentState) -> dict:
        last_message = state["messages"][-1]
        text = last_message["content"] if isinstance(last_message, dict) else last_message.content

        for guard in (pii_guard, injection_guard):
            result = guard.check(text)

            if not result.passed and result.severity == "block":
                logger.warning("guardrail_blocked", guard=guard.name, reason=result.reason)
                return {
                    "messages": [{
                        "role": "assistant",
                        "content": "I'm not able to process that message. Please rephrase your request without sensitive information or unusual instructions.",
                    }],
                    "blocked": True,
                }

            if result.severity == "warn":
                logger.info("guardrail_warning", guard=guard.name, reason=result.reason)

        return {"blocked": False}

    return input_guard_node