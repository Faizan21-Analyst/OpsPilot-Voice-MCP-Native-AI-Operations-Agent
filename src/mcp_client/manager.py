from src.mcp_client.connection import MCPConnection
from src.mcp_client.circuit_breaker import CircuitBreaker
from src.core.logging import get_logger

logger = get_logger(__name__)


class MCPClientManager:
    def __init__(self, server_configs: dict[str, str]):
        """server_configs: {"docs": "http://127.0.0.1:8001/mcp", "sql": "...", "ops": "..."}"""
        self._connections: dict[str, MCPConnection] = {
            name: MCPConnection(name, url) for name, url in server_configs.items()
        }
        self._breakers: dict[str, CircuitBreaker] = {
            name: CircuitBreaker() for name in server_configs
        }

    async def discover_tools(self) -> list[dict]:
        all_tools = []
        for server_name, conn in self._connections.items():
            try:
                tools = await conn.list_tools()
                for tool in tools:
                    tool["server"] = server_name   # tag which server owns it
                all_tools.extend(tools)
            except Exception as e:
                logger.error("tool_discovery_failed", server=server_name, error=str(e))
        return all_tools

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict) -> dict:
        breaker = self._breakers[server_name]
        connection = self._connections[server_name]

        if not breaker.allow_request():
            logger.warning("circuit_open_rejected", server=server_name, tool=tool_name)
            return {"success": False, "error": f"{server_name} is currently unavailable"}

        try:
            result = await connection.call_tool(tool_name, arguments)
            breaker.record_success()
            return result
        except Exception as e:
            breaker.record_failure()
            logger.error("mcp_call_failed", server=server_name, tool=tool_name, error=str(e))
            return {"success": False, "error": str(e)}

    async def health_check_all(self) -> dict[str, bool]:
        return {
            name: await conn.health_check()
            for name, conn in self._connections.items()
        }