from src.agent.state import AgentState
from src.guardrails.output.pii_leak_guard import PIILeakGuard
from src.guardrails.output.grounding_guard import GroundingGuard
from src.core.logging import get_logger

logger = get_logger(__name__)


def build_output_guard_node(pii_leak_guard: PIILeakGuard, grounding_guard: GroundingGuard):

    async def output_guard_node(state: AgentState) -> dict:
        last_message = state["messages"][-1]
        text = last_message["content"] if isinstance(last_message, dict) else last_message.content
        tools_called = state.get("tools_called_this_turn", [])

        pii_result = pii_leak_guard.check(text)
        if not pii_result.passed:
            logger.warning("output_guardrail_blocked", guard=pii_leak_guard.name, reason=pii_result.reason)
            return {
                "messages": [{
                    "role": "assistant",
                    "content": "I need to revise that response — it may have included information it shouldn't have. Could you rephrase your question?",
                }],
            }

        grounding_result = grounding_guard.check(text, context={"tools_called_this_turn": tools_called})
        if grounding_result.severity == "warn" and not grounding_result.passed is False:
            logger.info("output_guardrail_warning", guard=grounding_guard.name, reason=grounding_result.reason)

        return {}

    return output_guard_node