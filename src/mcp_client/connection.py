"""
Wraps a connection to ONE MCP server, built with fastmcp's Client.
MCPClientManager (next file) holds three of these — one per server.
"""
from fastmcp import Client

from src.core.logging import get_logger

logger = get_logger(__name__)


class MCPConnection:
    def __init__(self, name: str, url: str):
        self.name = name
        self.url = url
        self._client = Client(url)

    async def list_tools(self) -> list[dict]:
        """Ask the server what tools it exposes. Used at startup for tool discovery."""
        async with self._client:
            tools = await self._client.list_tools()
            return [
                {
                    "name": t.name,
                    "description": t.description,
                    "input_schema": t.input_schema,  # fixed: was t.inputSchema
                }
                for t in tools
            ]

    async def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """Call one tool on this server and return its result."""
        logger.info("mcp_tool_call_start", server=self.name, tool=tool_name)
        async with self._client:
            result = await self._client.call_tool(tool_name, arguments)
        logger.info("mcp_tool_call_done", server=self.name, tool=tool_name)
        return result

    async def health_check(self) -> bool:
        """
        Quick check: is this server reachable right now?
        fastmcp servers don't all implement the raw 'ping' RPC, so use
        list_tools() as the health probe instead — it's cheap and every
        MCP server must support it.
        """
        try:
            async with self._client:
                await self._client.list_tools()
            return True
        except Exception as e:
            logger.warning("mcp_server_unreachable", server=self.name, error=str(e))
            return False