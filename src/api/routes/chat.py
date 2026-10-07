from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from langgraph.types import Command

from src.auth.dependencies import get_current_principal
from src.auth.models import TokenPayload

router = APIRouter()


def _thread_id(principal: TokenPayload) -> str:
    """The conversation (LangGraph thread) for this user's text chat.

    It is derived from the authenticated user, never from anything the browser sends. Trusting a client-supplied id
    would let one logged-in user read another user's conversation or answer another user's pending approval.
    """
    return f"web-text-{principal.user_id}"


class ChatRequest(BaseModel):
    message: str
    # Accepted so existing clients keep working, but ignored: the server decides the conversation.
    session_id: str | None = None


class ChatResumeRequest(BaseModel):
    approved: bool
    # Accepted so existing clients keep working, but ignored: the server decides the conversation.
    session_id: str | None = None


@router.post("/chat")
async def chat(body: ChatRequest, request: Request, principal: TokenPayload = Depends(get_current_principal)):
    container = request.app.state.container
    agent = container.agent
    manager = container.manager

    session_id = _thread_id(principal)
    available_tools = await manager.discover_tools()

    initial_state = {
        "messages": [{"role": "user", "content": body.message}],
        "session_id": session_id,
        "principal": {"user_id": principal.user_id, "role": principal.role},
        "available_tools": available_tools,
    }
    config = {"configurable": {"thread_id": session_id}}

    result = await agent.ainvoke(initial_state, config=config)

    if "__interrupt__" in result:
        payload = result["__interrupt__"][0].value
        return {"status": "approval_required", "approval": payload}

    final_message = result["messages"][-1]
    answer = final_message.get("content") if isinstance(final_message, dict) else final_message.content
    return {"status": "ok", "answer": answer}


@router.post("/chat/resume")
async def chat_resume(body: ChatResumeRequest, request: Request, principal: TokenPayload = Depends(get_current_principal)):
    container = request.app.state.container
    agent = container.agent
    config = {"configurable": {"thread_id": _thread_id(principal)}}

    result = await agent.ainvoke(Command(resume={"approved": body.approved}), config=config)

    final_message = result["messages"][-1]
    answer = final_message.get("content") if isinstance(final_message, dict) else final_message.content
    return {"status": "ok", "answer": answer}
