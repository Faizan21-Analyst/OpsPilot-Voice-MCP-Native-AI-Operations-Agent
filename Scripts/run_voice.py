import asyncio

from src.core.config import load_settings
from src.providers.llm.litellm_provider import LiteLLMProvider
from src.mcp_client.manager import MCPClientManager
from src.permissions.policy_engine import PolicyEngine
from src.agent.graph import build_agent_graph
from src.channels.voice.pipeline import build_voice_pipeline
import requests
from src.auth.security import decode_access_token

async def main():
    settings = load_settings()

    llm = LiteLLMProvider(settings.groq, settings.gemini, settings.litellm)

    manager = MCPClientManager({
        "docs": "http://127.0.0.1:8001/mcp",
        "sql": "http://127.0.0.1:8002/mcp",
        "ops": "http://127.0.0.1:8003/mcp",
    })

    policy = PolicyEngine("policies/tools.yaml")

    agent = build_agent_graph(llm, manager, policy)


   

    resp = requests.post("http://localhost:8000/login", json={"username": "admin", "password": "admin123"})
    token = resp.json()["access_token"]
    payload = decode_access_token(token, settings.auth)

    from src.channels.voice.pipeline import build_local_voice_pipeline

    pipeline, worker, runner = await build_local_voice_pipeline(
        settings=settings,
        agent=agent,
        manager=manager,
        principal={"user_id": payload["user_id"], "role": payload["role"]},
        session_id="voice-session-prod",
    )

    await runner.run()


if __name__ == "__main__":
    asyncio.run(main())