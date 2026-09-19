import json
from src.agent.state import AgentState
from src.interfaces.llm import BaseLLMProvider

_TRUSTED_INJECTED_FIELDS = {"requested_by", "requester_role", "idempotency_key"}


def _strip_trusted_fields(schema: dict) -> dict:
    schema = json.loads(json.dumps(schema))  # deep copy — never mutate the original
    properties = schema.get("properties", {})
    for field in _TRUSTED_INJECTED_FIELDS:
        properties.pop(field, None)
    if "required" in schema:
        schema["required"] = [r for r in schema["required"] if r not in _TRUSTED_INJECTED_FIELDS]
    return schema


def _convert_tools(available_tools: list[dict]) -> list[dict]:
    tools_schema = []
    for tool in available_tools:
        parameters = tool["input_schema"]
        if tool.get("server") == "ops":
            parameters = _strip_trusted_fields(parameters)

        tools_schema.append({
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"] or "",
                "parameters": parameters,
            },
        })
    return tools_schema

def build_planner_node(llm: BaseLLMProvider):

    async def planner_node(state: AgentState) -> dict:
        tools_schema = _convert_tools(state["available_tools"])

        api_messages = []
        for msg in state["messages"]:
            if isinstance(msg, dict):
                role = msg.get("role", "user")
                m_dict = {"role": role, "content": msg.get("content", "")}
                if "tool_call_id" in msg:
                    m_dict["tool_call_id"] = msg["tool_call_id"]
            else:
                role = msg.type
                if role == "human": role = "user"
                elif role == "ai": role = "assistant"
                elif role == "tool": role = "tool"
                
                m_dict = {"role": role, "content": msg.content or ""}
                
                if hasattr(msg, "tool_call_id") and msg.tool_call_id:
                    m_dict["tool_call_id"] = msg.tool_call_id
                
                # --- DEFINITIVE FIX: Rebuild strict Groq format for tool calls in history ---
                if hasattr(msg, "tool_calls") and msg.tool_calls:
                    groq_tool_calls = []
                    for tc in msg.tool_calls:
                        groq_tool_calls.append({
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(tc["args"])
                            }
                        })
                    m_dict["tool_calls"] = groq_tool_calls
                # ----------------------------------------------------------------------------
                    
            api_messages.append(m_dict)

        response = await llm.generate(
            messages=api_messages,
            tools=tools_schema,
        )

        assistant_message = {
            "role": "assistant",
            "content": response.content or "",
        }

        if response.tool_calls:
            formatted_tool_calls = []
            for tc in response.tool_calls:
                tc_id = getattr(tc, "id", None)
                if hasattr(tc, "function"):
                    tc_name = tc.function.name
                    raw_args = tc.function.arguments
                else:
                    tc_name = getattr(tc, "name", "")
                    raw_args = getattr(tc, "arguments", getattr(tc, "args", "{}"))

                if isinstance(raw_args, str):
                    try:
                        args_dict = json.loads(raw_args)
                    except Exception:
                        args_dict = {}
                else:
                    args_dict = raw_args or {}

                formatted_tool_calls.append({
                    "id": tc_id,
                    "name": tc_name,
                    "args": args_dict,
                })
            
            assistant_message["tool_calls"] = formatted_tool_calls

        return {"messages": [assistant_message]}

    return planner_node