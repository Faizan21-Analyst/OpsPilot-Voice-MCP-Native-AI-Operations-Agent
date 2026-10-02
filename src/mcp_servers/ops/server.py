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
from src.mcp_servers.ops import project_tools

logger = get_logger(__name__)
mcp = FastMCP("ops-server")

settings = load_settings()
configure_logging(settings.log_level)

engine = build_engine(settings.database)
session_factory = build_session_factory(engine)

# The ops tools use the project/account tables, so make sure they exist even if this server starts before the API.
# Idempotent and safe to run from several processes at once.
try:
    from src.mcp_servers.sql.migrate import migrate_database
    migrate_database(settings.database.url)
except Exception as e:
    logger.error("ops_db_migration_failed", error=str(e))

# NOTE: requested_by, requester_role, idempotency_key and reason are injected by the agent's executor
# from the authenticated session - the model never supplies (or sees) them.


@mcp.tool()
async def create_ticket_tool(
    employee_id: str,
    requested_by: str,
    requester_role: str,
    category: str,
    description: str,
    idempotency_key: str,
    reason: str = "",
) -> dict:
    """Create an IT support ticket on behalf of an employee."""
    try:
        result = await create_ticket(
            session_factory, employee_id, requested_by, requester_role,
            category, description, idempotency_key, reason,
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
    reason: str = "",
) -> dict:
    """Approve a password reset for an employee (self-service, or admin for others). The employee then types
    the new password into the 'Set new password' box on the page - never ask for a password in chat."""
    try:
        result = await reset_password(
            session_factory, employee_id, requested_by, requester_role, idempotency_key, reason,
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
    reason: str = "",
) -> dict:
    """Revoke an employee's system access. Admin-only."""
    try:
        result = await revoke_access(
            session_factory, employee_id, requested_by, requester_role, idempotency_key, reason,
        )
        logger.info("ops_tool_called", tool="revoke_access", success=result["success"])
        return result
    except Exception as e:
        logger.error("ops_tool_failed", tool="revoke_access", error=str(e))
        return {"success": False, "reason": "internal_error"}


# ---------------------------------------------------------------- projects -----------------------
@mcp.tool()
async def list_projects_tool(
    requested_by: str,
    requester_role: str,
    idempotency_key: str,
    employee_id: str | None = None,
    status: str | None = None,
    reason: str = "",
) -> dict:
    """List projects. Employees see only the projects they are assigned to; admins see all (optionally for one
    employee_id) and can filter by status: active or completed."""
    try:
        return await project_tools.list_projects(session_factory, requested_by, requester_role, employee_id, status)
    except Exception as e:
        logger.error("ops_tool_failed", tool="list_projects", error=str(e))
        return {"success": False, "reason": "internal_error"}


@mcp.tool()
async def get_project_status_tool(
    requested_by: str,
    requester_role: str,
    idempotency_key: str,
    project: str,
    reason: str = "",
) -> dict:
    """Where is a project? Takes a project id (P001) or name. Returns stage, progress, who is doing what, recent
    activity and a `flowchart` text diagram. Show the flowchart to the user exactly as given, inside a code block."""
    try:
        return await project_tools.get_project_status(session_factory, requested_by, requester_role, project)
    except Exception as e:
        logger.error("ops_tool_failed", tool="get_project_status", error=str(e))
        return {"success": False, "reason": "internal_error"}


@mcp.tool()
async def create_project_tool(
    requested_by: str,
    requester_role: str,
    idempotency_key: str,
    name: str,
    description: str = "",
    priority: str = "medium",
    due_date: str | None = None,
    assignee_ids: list[str] | None = None,
    reason: str = "",
) -> dict:
    """Create a project (admin only). priority: low, medium, high or critical. due_date: YYYY-MM-DD.
    assignee_ids: optional employee ids or names to put on the team."""
    try:
        return await project_tools.create_project(
            session_factory, requested_by, requester_role, name, description, priority, due_date, assignee_ids, reason)
    except Exception as e:
        logger.error("ops_tool_failed", tool="create_project", error=str(e))
        return {"success": False, "reason": "internal_error"}


@mcp.tool()
async def assign_project_tool(
    employee_id: str,
    requested_by: str,
    requester_role: str,
    idempotency_key: str,
    project: str,
    role_in_project: str = "Member",
    reason: str = "",
) -> dict:
    """Assign an employee (id or name) to a project (id or name). Admin only. The project then shows up under
    that employee's name in the database."""
    try:
        return await project_tools.assign_project(
            session_factory, requested_by, requester_role, project, employee_id, role_in_project, reason)
    except Exception as e:
        logger.error("ops_tool_failed", tool="assign_project", error=str(e))
        return {"success": False, "reason": "internal_error"}


@mcp.tool()
async def update_project_progress_tool(
    requested_by: str,
    requester_role: str,
    idempotency_key: str,
    project: str,
    update_type: str,
    message: str = "",
    progress_pct: int | None = None,
    reason: str = "",
) -> dict:
    """The signed-in employee reports on a project they are assigned to. update_type: 'progress' (working on it,
    optionally progress_pct 0-100), 'blocked' (message says what is blocking), 'done' (they finished their part)
    or 'note'. This updates the project's flow chart for the admin."""
    try:
        return await project_tools.update_project_progress(
            session_factory, requested_by, requester_role, project, update_type, message, progress_pct, reason)
    except Exception as e:
        logger.error("ops_tool_failed", tool="update_project_progress", error=str(e))
        return {"success": False, "reason": "internal_error"}


@mcp.tool()
async def set_project_stage_tool(
    requested_by: str,
    requester_role: str,
    idempotency_key: str,
    project: str,
    stage: str,
    reason: str = "",
) -> dict:
    """Move a project to a stage: planning, in_progress, review or done (done = signed off). Admin only."""
    try:
        return await project_tools.set_project_stage(session_factory, requested_by, requester_role, project, stage, reason)
    except Exception as e:
        logger.error("ops_tool_failed", tool="set_project_stage", error=str(e))
        return {"success": False, "reason": "internal_error"}


if __name__ == "__main__":
    mcp.run(transport="streamable-http", port=8003)
