import asyncio

from langgraph.types import Command

from src.permissions.policy_engine import PolicyEngine
from src.core.config import load_settings
from src.providers.llm.litellm_provider import LiteLLMProvider
from src.mcp_client.manager import MCPClientManager
from src.agent.graph import build_agent_graph
from src.core.tracing import setup_tracing
setup_tracing()

async def main():
    settings = load_settings()
    llm = LiteLLMProvider(settings.groq, settings.gemini, settings.litellm)

    manager = MCPClientManager({
        "docs": "http://127.0.0.1:8001/mcp",
        "sql": "http://127.0.0.1:8002/mcp",
        "ops": "http://127.0.0.1:8003/mcp",
    })
    policy = PolicyEngine("policies/tool.yaml")  

    available_tools = await manager.discover_tools()
    print("Discovered tools:", [t["name"] for t in available_tools])

    agent = build_agent_graph(llm, manager, policy)

    initial_state = {
        "messages": [
            {"role": "user", "content": "hey what is prometheus?"}
        ],
        "session_id": "test-session-approval",
        "principal": {"user_id": "E001", "role": "admin"},
        "available_tools": available_tools,
    }

    config = {"configurable": {"thread_id": "test-session-approval"}}

    result = await agent.ainvoke(initial_state, config=config)
    
    if "__interrupt__" in result:
        interrupt_payload = result["__interrupt__"][0].value
        print("\n--- APPROVAL REQUIRED ---")
        print(interrupt_payload)

        answer = input("\nApprove this action? (y/n): ").strip().lower()
        approved = answer == "y"

        print(f"\n--- Resuming with approved={approved} ---")
        result = await agent.ainvoke(Command(resume={"approved": approved}), config=config)
    print("\n--- Final conversation ---")
    for m in result["messages"]:
        content = m.get("content") if isinstance(m, dict) else getattr(m, "content", None)
        print(content)


if __name__ == "__main__":
    asyncio.run(main())