import json

from langgraph.types import interrupt

from src.agent.state import AgentState
from src.mcp_client.manager import MCPClientManager
from src.permissions.policy_engine import PolicyEngine


def _last_user_request(state: AgentState, limit: int = 300) -> str:
    """The most recent thing the human said. Recorded as the 'why' on every Ops action."""
    for msg in reversed(state["messages"]):
        if isinstance(msg, dict):
            role, content = msg.get("role"), msg.get("content")
        else:
            role, content = getattr(msg, "type", None), getattr(msg, "content", None)
        if role in ("user", "human") and isinstance(content, str) and content.strip():
            return " ".join(content.split())[:limit]
    return ""


def build_executor_node(manager: MCPClientManager,policy_engine: PolicyEngine,):
    async def executor_node(state: AgentState) -> dict:

        tool_name_to_server = {
            tool["name"]: tool["server"]
            for tool in state["available_tools"]
        }

        principal = state["principal"]

        last_message = state["messages"][-1]

        tool_calls = (
            last_message.get("tool_calls", [])
            if isinstance(last_message, dict)
            else getattr(last_message, "tool_calls", [])
        )

        tool_result_messages = []

        tools_called = []

        def inject_trusted_fields(tool_args: dict, tool_call_id: str) -> None:
            # Never taken from the model: identity, idempotency and the human's request (the audit "why").
            tool_args["requested_by"] = principal["user_id"]
            tool_args["requester_role"] = principal["role"]
            tool_args["idempotency_key"] = f"{state['session_id']}:{tool_call_id}"
            tool_args["reason"] = _last_user_request(state)

        for call in tool_calls:

            tool_name = call["name"]
            tool_args = dict(call["args"])
            tool_call_id = call["id"]

            # Track the tool
            tools_called.append(tool_name)

            server_name = tool_name_to_server.get(tool_name)

            if server_name is None:

                content_str = json.dumps({
                    "success": False,
                    "error": f"Unknown tool: {tool_name}",
                })

                tool_result_messages.append({
                    "role": "tool",
                    "name": tool_name,
                    "tool_call_id": tool_call_id,
                    "content": content_str,
                })

                continue

            target_employee_id = tool_args.get(
                "employee_id",
                principal["user_id"],
            )

            decision = policy_engine.evaluate(
                tool_name=tool_name,
                employee_id=target_employee_id,
                requested_by=principal["user_id"],
                requester_role=principal["role"],
            )

            if not decision.allowed:

                result = {
                    "success": False,
                    "reason": decision.reason,
                }

            elif decision.requires_approval:

                approval = interrupt({
                    "type": "approval_required",
                    "tool": tool_name,
                    "arguments": tool_args,
                    "requested_by": principal["user_id"],
                    "reason": decision.reason,
                })

                if not approval.get("approved"):

                    result = {
                        "success": False,
                        "reason": "denied_by_human_approver",
                    }

                else:

                    if server_name == "ops":
                        inject_trusted_fields(tool_args, tool_call_id)

                    result = await manager.call_tool(
                        server_name,
                        tool_name,
                        tool_args,
                    )

            else:

                if server_name == "ops":
                    inject_trusted_fields(tool_args, tool_call_id)

                result = await manager.call_tool(
                    server_name,
                    tool_name,
                    tool_args,
                )

            content_str = (
                json.dumps(result)
                if isinstance(result, dict)
                else str(result)
            )

            tool_result_messages.append({
                "role": "tool",
                "name": tool_name,
                "tool_call_id": tool_call_id,
                "content": content_str,
            })

        return {
            "messages": tool_result_messages,
            "tools_called_this_turn": tools_called,
        }

    return executor_node
