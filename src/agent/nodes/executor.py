import json
from src.agent.state import AgentState
from src.mcp_client.manager import MCPClientManager


def build_executor_node(manager: MCPClientManager):

    async def executor_node(state: AgentState) -> dict:
        tool_name_to_server = {
            tool["name"]: tool["server"]
            for tool in state["available_tools"]
        }
        principal = state["principal"]

        last_message = state["messages"][-1]

        if isinstance(last_message, dict):
            tool_calls = last_message.get("tool_calls", [])
        else:
            tool_calls = getattr(last_message, "tool_calls", [])

        tool_result_messages = []

        for call in tool_calls:
            tool_name = call["name"]
            tool_args = dict(call["args"])  # copy — never mutate the LLM's original
            tool_call_id = call["id"]

            server_name = tool_name_to_server.get(tool_name)

            if server_name is None:
                content_str = json.dumps({
                    "success": False,
                    "error": f"Unknown tool: {tool_name}",
                })
            else:
                # --- Trusted-context injection ---
                # requested_by / requester_role / idempotency_key must NEVER
                # come from the LLM. The LLM never even sees these fields
                # (planner.py strips them from the schema for ops tools).
                # They're injected here from server-side trusted state only.
                if server_name == "ops":
                    tool_args["requested_by"] = principal["user_id"]
                    tool_args["requester_role"] = principal["role"]
                    tool_args["idempotency_key"] = f"{state['session_id']}:{tool_call_id}"

                result = await manager.call_tool(
                    server_name=server_name,
                    tool_name=tool_name,
                    arguments=tool_args,
                )

                try:
                    if hasattr(result, "content") and isinstance(result.content, list):
                        content_str = "\n".join(
                            getattr(item, "text", str(item))
                            for item in result.content
                        )
                    elif hasattr(result, "model_dump_json"):
                        content_str = result.model_dump_json()
                    elif isinstance(result, dict):
                        content_str = json.dumps(result)
                    else:
                        content_str = str(result)
                except Exception:
                    content_str = str(result)

            tool_result_messages.append(
                {
                    "role": "tool",
                    "name": tool_name,
                    "tool_call_id": tool_call_id,
                    "content": content_str,
                }
            )

        return {"messages": tool_result_messages}

    return executor_node