from src.permissions.policy_engine import PolicyEngine

def test_admin_only_denies_employee():
    engine = PolicyEngine("C:/Users/ligio/OneDrive/Desktop/OpsPilot-Voice-MCP-Native-AI-Operations-Agent/policies/tool.yaml")
    decision = engine.evaluate("revoke_access_tool", employee_id="E002", requested_by="E001", requester_role="employee")
    assert decision.allowed is False

def test_destructive_requires_approval():
    engine = PolicyEngine("C:/Users/ligio/OneDrive/Desktop/OpsPilot-Voice-MCP-Native-AI-Operations-Agent/policies/tool.yaml")
    decision = engine.evaluate("reset_password_tool", employee_id="E001", requested_by="E001", requester_role="employee")
    assert decision.allowed is True
    assert decision.requires_approval is True