import asyncio
from src.mcp_client.manager import MCPClientManager

async def main():
    manager = MCPClientManager({
        "docs": "http://127.0.0.1:8001/mcp",
        "sql": "http://127.0.0.1:8002/mcp",
        "ops": "http://127.0.0.1:8003/mcp",
    })
    print(await manager.health_check_all())
    tools = await manager.discover_tools()
    for t in tools:
        print(t["server"], "->", t["name"])

    # test a real call
    result = await manager.call_tool("docs", "search_policy_docs", {"query": "how to install prometheus python client"})
    result2 = await manager.call_tool("sql", "get_employee_tool",  {'employee_id':"E002"})

    print(result)
    print(result2)

asyncio.run(main())