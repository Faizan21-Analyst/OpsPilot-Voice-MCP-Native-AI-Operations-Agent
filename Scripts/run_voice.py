import asyncio

from src.core.config import load_settings
from src.providers.llm.litellm_provider import LiteLLMProvider
from src.mcp_client.manager import MCPClientManager
from src.permissions.policy_engine import PolicyEngine
from src.agent.graph import build_agent_graph
from src.channels.voice.pipeline import build_voice_pipeline


async def main():
    settings = load_settings()

    llm = LiteLLMProvider(settings.groq, settings.gemini, settings.litellm)

    manager = MCPClientManager({
        "docs": "http://127.0.0.1:8001/mcp",
        "sql": "http://127.0.0.1:8002/mcp",
        "ops": "http://127.0.0.1:8003/mcp",
    })

    policy = PolicyEngine("policies/tool.yaml")

    agent = build_agent_graph(llm, manager, policy)

    pipeline, worker, runner = await build_voice_pipeline(
        settings=settings,
        agent=agent,
        manager=manager,
        principal={"user_id": "E002", "role": "admin"},
        session_id="voice-session-prod",
    )

    await runner.run()


if __name__ == "__main__":
    asyncio.run(main())