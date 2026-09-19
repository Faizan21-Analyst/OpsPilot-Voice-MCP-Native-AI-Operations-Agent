from typing import TypedDict, Annotated

from langgraph.graph.message import add_messages


class Principal(TypedDict):
    user_id: str
    role: str


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    session_id: str
    principal: Principal
    available_tools: list[dict]