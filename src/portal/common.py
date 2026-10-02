"""Shared helpers for the portal services (accounts, projects, reports)."""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select

from src.mcp_servers.ops.models import audit_log

_FMT = "%Y-%m-%d %H:%M:%S"


class ServiceError(Exception):
    """A user-facing business-rule failure. `status` maps to an HTTP status in the API layer."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


def now_dt() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    return (dt or now_dt()).strftime(_FMT)


def parse_ts(value) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        return datetime.strptime(str(value)[:19], _FMT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def clean(value):
    if isinstance(value, datetime):
        return value.strftime(_FMT)
    if isinstance(value, Decimal):
        return float(value)
    return value


def rows(result) -> list[dict]:
    return [{k: clean(v) for k, v in r.items()} for r in result.mappings().all()]


def one(result) -> dict | None:
    r = result.mappings().first()
    return {k: clean(v) for k, v in r.items()} if r else None


async def add_audit(
    session, *, action: str, employee_id: str, requested_by: str, status: str,
    detail: str = "", reason: str = "", actor_role: str = "", source: str = "portal",
    idempotency_key: str | None = None,
) -> None:
    """Insert an audit row in the caller's transaction (caller commits). A duplicate
    idempotency_key raises IntegrityError, which callers translate to 'duplicate_request'."""
    await session.execute(audit_log.insert().values(
        action=action, employee_id=employee_id, requested_by=requested_by,
        idempotency_key=idempotency_key or f"{source}:{uuid.uuid4().hex}",
        status=status, detail=(detail or "")[:500], reason=(reason or "")[:500],
        actor_role=actor_role, source=source,
    ))


async def employee_names(session, ids: set[str]) -> dict[str, str]:
    from src.mcp_servers.sql.models import employees
    ids = {i for i in ids if i}
    if not ids:
        return {}
    res = await session.execute(select(employees.c.employee_id, employees.c.name).where(employees.c.employee_id.in_(ids)))
    return {r[0]: r[1] for r in res.all()}
