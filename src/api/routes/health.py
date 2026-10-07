import asyncio
from urllib.parse import urlparse

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

router = APIRouter()


@router.get("/health")
async def health(request: Request):
    """Deep check: makes a real LLM call, so do not poll it frequently."""
    container = request.app.state.container
    return await container.health_check()


@router.get("/healthz")
async def healthz():
    """Cheap liveness probe for Docker / load balancers: no LLM call, no I/O."""
    return {"status": "ok"}


async def _port_open(url: str, timeout: float = 2.0) -> bool:
    """True if something accepts TCP connections at the host:port of an MCP server URL."""
    parsed = urlparse(url)
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(parsed.hostname, parsed.port), timeout)
    except (OSError, asyncio.TimeoutError):
        return False
    writer.close()
    return True


@router.get("/readyz")
async def readyz(request: Request):
    """Startup probe for Azure Container Apps (compose's `depends_on: service_healthy`, as an HTTP endpoint).

    Returns 200 only when the docs, sql and ops MCP servers all accept connections. The docs server opens its port only
    after its models are loaded, so this stays 503 for the first few minutes of a cold start. Otherwise a voice session
    that starts early would cache a tool list without the policy-search tool. No LLM call, no side effects."""
    container = getattr(request.app.state, "container", None)
    if container is None:
        return JSONResponse({"status": "starting"}, status_code=503)
    urls = {
        "docs": container.settings.mcp_servers.docs_url,
        "sql": container.settings.mcp_servers.sql_url,
        "ops": container.settings.mcp_servers.ops_url,
    }
    results = await asyncio.gather(*(_port_open(u) for u in urls.values()))
    status = dict(zip(urls, results))
    ready = all(results)
    return JSONResponse({"status": "ready" if ready else "waiting", "servers": status}, status_code=200 if ready else 503)
