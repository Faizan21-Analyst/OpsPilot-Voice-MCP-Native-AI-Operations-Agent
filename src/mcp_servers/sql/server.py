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
from src.mcp_servers.sql.tools import (
    get_employee,
    search_employees,
    get_tickets_for_employee,
    get_ticket,
)

logger = get_logger(__name__)
mcp = FastMCP("sql-server")

settings = load_settings()
configure_logging(settings.log_level)

engine = build_engine(settings.database)
session_factory = build_session_factory(engine)


@mcp.tool()
async def get_employee_tool(employee_id: str) -> dict | None:
    """Get an employee by their employee ID."""
    try:
        result = await get_employee(session_factory, employee_id)
        logger.info("sql_tool_called", tool="get_employee", found=result is not None)
        return result
    except Exception as e:
        logger.error("sql_tool_failed", tool="get_employee", error=str(e))
        return None


@mcp.tool()
async def search_employees_tool(
    name: str | None = None,
    department: str | None = None,
) -> list[dict]:
    """Search employees by optional name and department."""
    try:
        results = await search_employees(session_factory, name=name, department=department)
        logger.info("sql_tool_called", tool="search_employees", results=len(results))
        return results
    except Exception as e:
        logger.error("sql_tool_failed", tool="search_employees", error=str(e))
        return []


@mcp.tool()
async def get_tickets_for_employee_tool(
    employee_id: str,
    status: str | None = None,
) -> list[dict]:
    """Get tickets belonging to an employee."""
    try:
        results = await get_tickets_for_employee(session_factory, employee_id=employee_id, status=status)
        logger.info("sql_tool_called", tool="get_tickets_for_employee", results=len(results))
        return results
    except Exception as e:
        logger.error("sql_tool_failed", tool="get_tickets_for_employee", error=str(e))
        return []


@mcp.tool()
async def get_ticket_tool(ticket_id: str) -> dict | None:
    """Get a ticket by ticket ID."""
    try:
        result = await get_ticket(session_factory, ticket_id)
        logger.info("sql_tool_called", tool="get_ticket", found=result is not None)
        return result
    except Exception as e:
        logger.error("sql_tool_failed", tool="get_ticket", error=str(e))
        return None


if __name__ == "__main__":
    mcp.run(transport="streamable-http", port=8002)