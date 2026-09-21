import json

from langgraph.types import interrupt

from src.agent.state import AgentState
from src.mcp_client.manager import MCPClientManager
from src.permissions.policy_engine import PolicyEngine


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
                        tool_args["requested_by"] = principal["user_id"]
                        tool_args["requester_role"] = principal["role"]
                        tool_args["idempotency_key"] = (
                            f"{state['session_id']}:{tool_call_id}"
                        )

                    result = await manager.call_tool(
                        server_name,
                        tool_name,
                        tool_args,
                    )

            else:

                if server_name == "ops":
                    tool_args["requested_by"] = principal["user_id"]
                    tool_args["requester_role"] = principal["role"]
                    tool_args["idempotency_key"] = (
                        f"{state['session_id']}:{tool_call_id}"
                    )

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