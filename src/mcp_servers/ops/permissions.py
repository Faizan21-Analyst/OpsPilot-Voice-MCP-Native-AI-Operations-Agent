"""
Temporary, simple permission check for destructive Ops actions.
This will be replaced by the full PolicyEngine (policies/tools.yaml) later —
every function here has the exact signature the real PolicyEngine will have,
so the swap is a one-file change.
"""
from dataclasses import dataclass


@dataclass
class Decision:
    allowed: bool
    reason: str


def check_permission(
    action: str,
    employee_id: str,
    requested_by: str,
    requester_role: str,
) -> Decision:
    """
    Rule for now (placeholder):
    - A user can act on their OWN employee_id.
    - An 'admin' can act on ANY employee_id.
    - Everything else is denied.
    """
    if requester_role == "admin":
        return Decision(allowed=True, reason="admin_override")

    if requested_by == employee_id:
        return Decision(allowed=True, reason="self_service")

    return Decision(
        allowed=False,
        reason=f"scope_violation: '{requested_by}' cannot perform '{action}' on '{employee_id}'",
    )