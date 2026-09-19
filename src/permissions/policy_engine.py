from dataclasses import dataclass
from pathlib import Path
import yaml


@dataclass
class ToolPolicy:
    tool: str
    risk: str
    auto_approve: bool
    scope: str | None = None
    constraints: list[dict] | None = None


@dataclass
class Decision:
    allowed: bool
    reason: str
    requires_approval: bool = False


class PolicyEngine:
    def __init__(self, policy_file: str):
        self._policies: dict[str, ToolPolicy] = self._load(policy_file)

    def _load(self, policy_file: str) -> dict:
        raw = yaml.safe_load(Path(policy_file).read_text())
        
        tool_list = raw.get("tools", []) if isinstance(raw, dict) else raw
        
        return {entry["tool"]: ToolPolicy(**entry) for entry in tool_list}

    def evaluate(self,tool_name: str,employee_id: str,requested_by: str,requester_role: str,) -> Decision:
        policy = self._policies.get(tool_name)
        if policy is None:
            return Decision(allowed=False, reason=f"no_policy_defined_for:{tool_name}")

        if policy.scope == "admin_only" and requester_role != "admin":
            return Decision(allowed=False, reason=f"scope_violation: admin_only tool, requester_role={requester_role}")

        if policy.scope == "self_or_admin":
            if requester_role != "admin" and requested_by != employee_id:
                return Decision(allowed=False, reason=f"scope_violation: '{requested_by}' cannot act on '{employee_id}'")

        # risk-tier check
        if policy.risk == "DESTRUCTIVE" and not policy.auto_approve:
            return Decision(allowed=True, reason="requires_human_approval", requires_approval=True)

        return Decision(allowed=True, reason="auto_approved")