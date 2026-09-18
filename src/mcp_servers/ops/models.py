from sqlalchemy import Table, Column, Integer, String, DateTime, func

from src.mcp_servers.sql.models import metadata


audit_log = Table(
    "audit_log",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("action", String, nullable=False),
    Column("employee_id", String, nullable=False),
    Column("requested_by", String, nullable=False),
    Column("idempotency_key", String, nullable=False, unique=True),
    Column("status", String, nullable=False),
    Column("detail", String),
    Column("created_at", DateTime, server_default=func.now()),
)