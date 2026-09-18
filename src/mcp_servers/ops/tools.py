from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.exc import IntegrityError

from src.mcp_servers.sql.models import employees, tickets
from src.mcp_servers.ops.models import audit_log
from src.mcp_servers.ops.permissions import check_permission


async def _write_audit(
    session,
    action: str,
    employee_id: str,
    requested_by: str,
    idempotency_key: str,
    status: str,
    detail: str,
) -> bool:
    """
    Insert an audit row. Returns False if idempotency_key already exists
    (meaning: this exact action was already performed — do not repeat it).
    """
    try:
        await session.execute(
            audit_log.insert().values(
                action=action,
                employee_id=employee_id,
                requested_by=requested_by,
                idempotency_key=idempotency_key,
                status=status,
                detail=detail,
            )
        )
        await session.commit()
        return True
    except IntegrityError:
        await session.rollback()
        return False


async def create_ticket(
    session_factory: async_sessionmaker,
    employee_id: str,
    requested_by: str,
    requester_role: str,
    category: str,
    description: str,
    idempotency_key: str,
) -> dict:
    decision = check_permission("create_ticket", employee_id, requested_by, requester_role)

    async with session_factory() as session:
        if not decision.allowed:
            await _write_audit(
                session, "create_ticket", employee_id, requested_by,
                idempotency_key, "denied", decision.reason,
            )
            return {"success": False, "reason": decision.reason}

        wrote = await _write_audit(
            session, "create_ticket", employee_id, requested_by,
            idempotency_key, "success", f"category={category}",
        )
        if not wrote:
            return {"success": False, "reason": "duplicate_request (already processed)"}

        ticket_id = f"T{int(datetime.now(timezone.utc).timestamp())}"
        await session.execute(
            tickets.insert().values(
                ticket_id=ticket_id,
                employee_id=employee_id,
                title=category,
                description=description,
                status="open",
            )
        )
        await session.commit()
        return {"success": True, "ticket_id": ticket_id}


async def reset_password(
    session_factory: async_sessionmaker,
    employee_id: str,
    requested_by: str,
    requester_role: str,
    idempotency_key: str,
) -> dict:
    decision = check_permission("reset_password", employee_id, requested_by, requester_role)

    async with session_factory() as session:
        if not decision.allowed:
            await _write_audit(
                session, "reset_password", employee_id, requested_by,
                idempotency_key, "denied", decision.reason,
            )
            return {"success": False, "reason": decision.reason}

        wrote = await _write_audit(
            session, "reset_password", employee_id, requested_by,
            idempotency_key, "success", "password reset (mocked)",
        )
        if not wrote:
            return {"success": False, "reason": "duplicate_request (already processed)"}

        # Mocked: no real password system to call. In production this would
        # invoke an identity-provider API (e.g. Azure AD Graph API).
        return {"success": True, "detail": f"Password reset for {employee_id} (mocked)"}


async def revoke_access(
    session_factory: async_sessionmaker,
    employee_id: str,
    requested_by: str,
    requester_role: str,
    idempotency_key: str,
) -> dict:
    # Deliberately stricter: only admins may revoke access (no self-service).
    if requester_role != "admin":
        decision_reason = f"scope_violation: '{requested_by}' (role={requester_role}) cannot revoke access"
        async with session_factory() as session:
            await _write_audit(
                session, "revoke_access", employee_id, requested_by,
                idempotency_key, "denied", decision_reason,
            )
        return {"success": False, "reason": decision_reason}

    async with session_factory() as session:
        wrote = await _write_audit(
            session, "revoke_access", employee_id, requested_by,
            idempotency_key, "success", "access revoked (mocked)",
        )
        if not wrote:
            return {"success": False, "reason": "duplicate_request (already processed)"}

        await session.execute(
            update(employees)
            .where(employees.c.employee_id == employee_id)
            .values(account_locked=1)
        )
        await session.commit()
        return {"success": True, "detail": f"Access revoked for {employee_id} (mocked)"}