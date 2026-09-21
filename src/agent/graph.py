from langgraph.graph import StateGraph, END

from src.agent.state import AgentState
from src.agent.nodes.planner import build_planner_node
from src.agent.nodes.executor import build_executor_node
from src.agent.nodes.input_guard import build_input_guard_node
from src.agent.nodes.output_guard import build_output_guard_node

from src.guardrails.input.pii_guard import PIIGuard
from src.guardrails.input.injection_guard import InjectionGuard
from src.guardrails.output.pii_leak_guard import PIILeakGuard
from src.guardrails.output.grounding_guard import GroundingGuard

from langgraph.checkpoint.memory import MemorySaver


def route_after_input_guard(state: AgentState) -> str:
    if state.get("blocked", False):
        return END
    return "planner"


def route_after_planner(state: AgentState) -> str:
    last_message = state["messages"][-1]

    if isinstance(last_message, dict):
        tool_calls = last_message.get("tool_calls")
    else:
        tool_calls = getattr(last_message, "tool_calls", None)

    if tool_calls:
        return "executor"

    return "output_guard"  # changed from END — final answers now pass through output_guard


def build_agent_graph(llm, manager, policy_engine):

    graph = StateGraph(AgentState)

    pii_guard = PIIGuard()
    injection_guard = InjectionGuard()
    pii_leak_guard = PIILeakGuard()
    grounding_guard = GroundingGuard()

    graph.add_node(
        "input_guard",
        build_input_guard_node(
            pii_guard,
            injection_guard,
        ),
    )

    graph.add_node(
        "planner",
        build_planner_node(llm),
    )

    graph.add_node(
        "executor",
        build_executor_node(
            manager,
            policy_engine,
        ),
    )

    graph.add_node(
        "output_guard",
        build_output_guard_node(
            pii_leak_guard,
            grounding_guard,
        ),
    )

    graph.set_entry_point("input_guard")

    graph.add_conditional_edges(
        "input_guard",
        route_after_input_guard,
        {
            "planner": "planner",
            END: END,
        },
    )

    graph.add_conditional_edges(
        "planner",
        route_after_planner,
        {
            "executor": "executor",
            "output_guard": "output_guard",
        },
    )

    graph.add_edge(
        "executor",
        "planner",
    )

    graph.add_edge(
        "output_guard",
        END,
    )

    checkpointer = MemorySaver()

    return graph.compile(
        checkpointer=checkpointer,
    )