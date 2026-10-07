import asyncio
import time

from src.mcp_client.connection import MCPConnection
from src.mcp_client.circuit_breaker import CircuitBreaker
from src.core.logging import get_logger

logger = get_logger(__name__)

# How long a successful tool list is reused. Tools only change when an MCP server is redeployed, so a few minutes is
# plenty; call discover_tools(force=True) or invalidate_tools() to refresh sooner.
TOOLS_TTL_SECONDS = 300.0
# If a server was down during discovery we only keep the partial list briefly, so recovery is picked up quickly.
TOOLS_PARTIAL_TTL_SECONDS = 15.0


class MCPClientManager:
    def __init__(self, server_configs: dict[str, str], tools_ttl: float = TOOLS_TTL_SECONDS):
        """server_configs: {"docs": "http://127.0.0.1:8001/mcp", "sql": "...", "ops": "..."}"""
        self._connections: dict[str, MCPConnection] = {
            name: MCPConnection(name, url) for name, url in server_configs.items()
        }
        self._breakers: dict[str, CircuitBreaker] = {
            name: CircuitBreaker() for name in server_configs
        }
        self._tools_ttl = tools_ttl
        self._tools_cache: list[dict] | None = None
        self._tools_expires = 0.0
        self._tools_lock = asyncio.Lock()

    def _cache_valid(self) -> bool:
        return self._tools_cache is not None and time.monotonic() < self._tools_expires

    def invalidate_tools(self) -> None:
        """Forget the cached tool list; the next discover_tools() call asks the servers again."""
        self._tools_expires = 0.0

    async def _fetch_server_tools(self, server_name: str, conn: MCPConnection):
        try:
            tools = await conn.list_tools()
            for tool in tools:
                tool["server"] = server_name   # tag which server owns it
            return tools, False
        except Exception as e:
            logger.error("tool_discovery_failed", server=server_name, error=str(e))
            return [], True

    async def discover_tools(self, force: bool = False) -> list[dict]:
        """
        Tool list for all MCP servers, cached so a chat request does not query every server each time.
        The three servers are asked in parallel. A server that is down is skipped (and the partial result is cached
        only briefly); if every server fails we fall back to the last good list rather than returning nothing.
        """
        if not force and self._cache_valid():
            return list(self._tools_cache)

        async with self._tools_lock:
            if not force and self._cache_valid():   # another request refreshed it while we waited for the lock
                return list(self._tools_cache)

            results = await asyncio.gather(
                *(self._fetch_server_tools(name, conn) for name, conn in self._connections.items())
            )
            tools = [tool for server_tools, _ in results for tool in server_tools]
            any_failed = any(failed for _, failed in results)

            if tools:
                self._tools_cache = tools
                ttl = TOOLS_PARTIAL_TTL_SECONDS if any_failed else self._tools_ttl
                self._tools_expires = time.monotonic() + ttl
                logger.info("tools_discovered", count=len(tools), partial=any_failed, cached_for_s=ttl)
                return list(tools)

            if self._tools_cache is not None:
                logger.warning("tool_discovery_empty_using_stale_cache")
                return list(self._tools_cache)
            return []

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
