import json
import os
from src.agent.state import AgentState
from src.core.logging import get_logger
from src.interfaces.llm import BaseLLMProvider

log = get_logger(__name__)

# Filled in by the executor from the authenticated session, so the model never sees (or supplies) them.
# "reason" is the user's own request text, recorded in the audit log; showing it to the model only wasted tokens.
_TRUSTED_INJECTED_FIELDS = {"requested_by", "requester_role", "idempotency_key", "reason"}

# Hidden from non-admins to save prompt tokens (~500). This is NOT the security check: the policy engine and the
# service layer still enforce admin-only rules on every call. (revoke_access_tool stays visible on purpose, so an
# employee's attempt is still caught, denied and written to the audit log.)
_ADMIN_ONLY_TOOLS = {"create_project_tool", "assign_project_tool", "set_project_stage_tool"}

# ---- prompt size controls -------------------------------------------------------------------------------------
# Groq's free/on-demand tier limits INPUT tokens per minute (7,000 for some models); one oversized request is
# rejected every time and retrying it only wastes seconds. These keep every request comfortably under the limit.
# Token counts are estimates (~3.3 characters per token, deliberately a little pessimistic).
def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


PROMPT_TOKEN_BUDGET = _env_int("OPSPILOT_PROMPT_BUDGET", 5000)   # 0 disables budget fitting
OLD_TOOL_RESULT_CHARS = 300      # tool output from EARLIER turns is only needed as a hint
OLD_ANSWER_CHARS = 700           # so are long assistant answers from earlier turns
NEW_TOOL_RESULT_CHARS = 6000     # hard cap on any single tool result in the current turn
MIN_TOOL_RESULT_CHARS = 1500     # budget fitting never shrinks a current-turn result below this


def _est_tokens(obj) -> int:
    text = obj if isinstance(obj, str) else json.dumps(obj, separators=(",", ":"), ensure_ascii=False)
    return round(len(text) / 3.3)


def _clip(text: str, limit: int) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit].rstrip() + " ...[truncated]"


def _drop_titles(node):
    """FastMCP/pydantic schemas carry a human 'title' on every property. The model does not need them."""
    if isinstance(node, dict):
        return {k: _drop_titles(v) for k, v in node.items() if k != "title" or isinstance(v, dict)}
    if isinstance(node, list):
        return [_drop_titles(v) for v in node]
    return node


def _strip_trusted_fields(schema: dict) -> dict:
    schema = json.loads(json.dumps(schema))  # deep copy — never mutate the original
    properties = schema.get("properties", {})
    for field in _TRUSTED_INJECTED_FIELDS:
        properties.pop(field, None)
    if "required" in schema:
        schema["required"] = [r for r in schema["required"] if r not in _TRUSTED_INJECTED_FIELDS]
    return schema


def _convert_tools(available_tools: list[dict], role: str | None = None) -> list[dict]:
    tools_schema = []
    for tool in available_tools:
        if role is not None and role != "admin" and tool["name"] in _ADMIN_ONLY_TOOLS:
            continue
        parameters = tool["input_schema"]
        if tool.get("server") == "ops":
            parameters = _strip_trusted_fields(parameters)

        tools_schema.append({
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"] or "",
                "parameters": _drop_titles(parameters),
            },
        })
    return tools_schema

# Only the most recent messages are sent to the LLM. The checkpointer keeps the WHOLE thread, so without this every
# turn makes the prompt longer and every LLM call slower. 6-10 is a good range; override with OPSPILOT_MAX_HISTORY.
try:
    MAX_HISTORY_MESSAGES = max(4, int(os.getenv("OPSPILOT_MAX_HISTORY", "10")))
except ValueError:
    MAX_HISTORY_MESSAGES = 10


def _role_of(msg) -> str:
    if isinstance(msg, dict):
        return msg.get("role", "user")
    kind = getattr(msg, "type", "")
    return {"human": "user", "ai": "assistant"}.get(kind, kind)


def trim_history(messages: list, keep: int = MAX_HISTORY_MESSAGES) -> list:
    """
    Keep roughly the last `keep` messages, but always start at a USER message.
    Cutting in the middle of a tool exchange would leave a tool result without the assistant tool_call that
    produced it (the LLM API rejects that), so the window is moved forward to the next user message. If the current
    turn is itself longer than the window (many tool calls), it is extended back to the start of that turn.
    """
    if keep <= 0 or len(messages) <= keep:
        return messages
    start = len(messages) - keep
    i = start
    while i < len(messages) and _role_of(messages[i]) != "user":
        i += 1
    if i >= len(messages):          # no user message inside the window: back up to the one that began this turn
        i = start
        while i > 0 and _role_of(messages[i]) != "user":
            i -= 1
    return messages[i:]


def _to_api_message(msg) -> dict:
    """Convert one stored message (dict or LangChain object) into the strict format the LLM API expects."""
    if isinstance(msg, dict):
        role = msg.get("role", "user")
        m_dict = {"role": role, "content": msg.get("content", "")}
        if "tool_call_id" in msg:
            m_dict["tool_call_id"] = msg["tool_call_id"]
        return m_dict

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
    return m_dict


def _prepare_history(history: list, new_tool_chars: int) -> list[dict]:
    """API messages with older turns compressed. 'Older' = everything before the latest user message."""
    last_user = max((i for i, m in enumerate(history) if _role_of(m) == "user"), default=-1)
    out = []
    for idx, msg in enumerate(history):
        m = _to_api_message(msg)
        content = m.get("content") or ""
        if m["role"] == "tool":
            m["content"] = _clip(content, OLD_TOOL_RESULT_CHARS if idx < last_user else new_tool_chars)
        elif m["role"] == "assistant" and idx < last_user and "tool_calls" not in m:
            m["content"] = _clip(content, OLD_ANSWER_CHARS)
        out.append(m)
    return out


def fit_history_to_budget(history: list, fixed_tokens: int, budget: int = PROMPT_TOKEN_BUDGET):
    """
    Returns (api_messages, estimated_total_tokens, dropped_turns).
    If the prompt would exceed `budget`, drop the OLDEST earlier turns first (always at a user-message boundary so
    tool calls stay paired with their results), then shrink the current turn's tool results. The current user
    message and its tool calls are never removed.
    """
    new_tool_chars, dropped = NEW_TOOL_RESULT_CHARS, 0
    while True:
        api = _prepare_history(history, new_tool_chars)
        used = fixed_tokens + _est_tokens(api)
        if not budget or used <= budget:
            return api, used, dropped
        nxt = next((i for i in range(1, len(history)) if _role_of(history[i]) == "user"), None)
        if nxt is not None:                       # another (newer) user message exists -> drop the oldest turn
            history, dropped = history[nxt:], dropped + 1
            continue
        if new_tool_chars > MIN_TOOL_RESULT_CHARS:  # only the current turn is left -> shrink its tool output
            new_tool_chars = max(MIN_TOOL_RESULT_CHARS, new_tool_chars // 2)
            continue
        return api, used, dropped                 # nothing left to cut; send what we have


def build_planner_node(llm: BaseLLMProvider):

    async def planner_node(state: AgentState) -> dict:
        principal = state["principal"]
        tools_schema = _convert_tools(state["available_tools"], principal["role"])
        system_message = {
        "role": "system",
        "content": (
            f"You are OpsPilot, an IT helpdesk assistant. "
            f"The current user is employee_id='{principal['user_id']}', role='{principal['role']}'. "
            f"When the user refers to 'my account', 'my password', 'me', etc., use their "
            f"employee_id '{principal['user_id']}' as the employee_id argument — do not ask them for it. "
            f"Only ask for an employee_id if they're asking about someone else and haven't named who."
            " FORMATTING: reply in markdown. Use markdown tables for tabular data. "
            "FLOW DIAGRAMS: never use mermaid. If a tool result has a 'flowchart' field, copy it exactly as given, unchanged, inside ONE ``` code block. "
            "For any other process or flow request, draw ONE plain-text box-and-arrow diagram (box-drawing characters like ┌ ─ ┐ │ └ ┘ ▼) inside ONE ``` code block, with no second copy. "
            "PROJECTS: employees can ask for their projects and tickets, and can say what they have done on a project (use update_project_progress_tool with progress, blocked or done). "
            "Admins can create projects, assign people, move stages and ask where a project is (use get_project_status_tool). "
            "PASSWORDS: never ask for or accept a password in chat or voice. After a reset is approved, tell the user to type the new password in the 'Set new password' box on the page."
        ),
    }

        fixed_tokens = _est_tokens(system_message) + _est_tokens(tools_schema)
        history_messages, total_tokens, dropped_turns = fit_history_to_budget(
            trim_history(state["messages"]), fixed_tokens
        )
        api_messages = [system_message] + history_messages
        log.info(
            "planner_prompt_size",
            approx_tokens=total_tokens,
            fixed_tokens=fixed_tokens,
            tools=len(tools_schema),
            history_messages=len(history_messages),
            dropped_old_turns=dropped_turns,
        )

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
