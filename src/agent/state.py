from typing import TypedDict, Annotated
import operator

from langgraph.graph.message import add_messages


class Principal(TypedDict):
    user_id: str
    role: str


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    session_id: str
    principal: Principal
    available_tools: list[dict]

    blocked: bool

    tools_called_this_turn: Annotated[
        list[str],
        operator.add,
    ]