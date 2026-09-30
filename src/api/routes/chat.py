from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from langgraph.types import Command

from src.auth.dependencies import get_current_principal
from src.auth.models import TokenPayload

router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    session_id: str = "web-{user_id}"


class ChatResumeRequest(BaseModel):
    session_id: str
    approved: bool


@router.post("/chat")
async def chat(body: ChatRequest, request: Request, principal: TokenPayload = Depends(get_current_principal)):
    container = request.app.state.container
    agent = container.agent
    manager = container.manager

    available_tools = await manager.discover_tools()

    initial_state = {
        "messages": [{"role": "user", "content": body.message}],
        "session_id": body.session_id,
        "principal": {"user_id": principal.user_id, "role": principal.role},
        "available_tools": available_tools,
    }
    config = {"configurable": {"thread_id": body.session_id}}

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
    config = {"configurable": {"thread_id": body.session_id}}

    result = await agent.ainvoke(Command(resume={"approved": body.approved}), config=config)

    final_message = result["messages"][-1]
    answer = final_message.get("content") if isinstance(final_message, dict) else final_message.content
    return {"status": "ok", "answer": answer}