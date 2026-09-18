import sys
import os
from pathlib import Path

# 1. Force Python to recognize your root directory
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if os.getcwd() not in sys.path:
    sys.path.insert(0, os.getcwd())

from fastmcp import FastMCP

from src.core.config import load_settings
from src.core.logging import get_logger, configure_logging
from src.mcp_servers.sql.database import build_engine, build_session_factory
from src.mcp_servers.ops.tools import create_ticket, reset_password, revoke_access

logger = get_logger(__name__)
mcp = FastMCP("ops-server")

settings = load_settings()
configure_logging(settings.log_level)

engine = build_engine(settings.database)
session_factory = build_session_factory(engine)


@mcp.tool()
async def create_ticket_tool(
    employee_id: str,
    requested_by: str,
    requester_role: str,
    category: str,
    description: str,
    idempotency_key: str,
) -> dict:
    """Create an IT support ticket on behalf of an employee."""
    try:
        result = await create_ticket(
            session_factory, employee_id, requested_by, requester_role,
            category, description, idempotency_key,
        )
        logger.info("ops_tool_called", tool="create_ticket", success=result["success"])
        return result
    except Exception as e:
        logger.error("ops_tool_failed", tool="create_ticket", error=str(e))
        return {"success": False, "reason": "internal_error"}


@mcp.tool()
async def reset_password_tool(
    employee_id: str,
    requested_by: str,
    requester_role: str,
    idempotency_key: str,
) -> dict:
    """Reset an employee's password. Self-service or admin-only for others."""
    try:
        result = await reset_password(
            session_factory, employee_id, requested_by, requester_role, idempotency_key,
        )
        logger.info("ops_tool_called", tool="reset_password", success=result["success"])
        return result
    except Exception as e:
        logger.error("ops_tool_failed", tool="reset_password", error=str(e))
        return {"success": False, "reason": "internal_error"}


@mcp.tool()
async def revoke_access_tool(
    employee_id: str,
    requested_by: str,
    requester_role: str,
    idempotency_key: str,
) -> dict:
    """Revoke an employee's system access. Admin-only."""
    try:
        result = await revoke_access(
            session_factory, employee_id, requested_by, requester_role, idempotency_key,
        )
        logger.info("ops_tool_called", tool="revoke_access", success=result["success"])
        return result
    except Exception as e:
        logger.error("ops_tool_failed", tool="revoke_access", error=str(e))
        return {"success": False, "reason": "internal_error"}


if __name__ == "__main__":
    mcp.run(transport="streamable-http", port=8003)