from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.exc import IntegrityError

from src.mcp_servers.sql.models import employees, tickets, ticket_events
from src.mcp_servers.ops.models import audit_log
from src.mcp_servers.ops.permissions import check_permission
from src.portal.accounts import create_password_reset, record_revoke_lock, RESET_TTL_MIN
from src.portal.common import iso


async def _write_audit(
    session,
    action: str,
    employee_id: str,
    requested_by: str,
    idempotency_key: str,
    status: str,
    detail: str,
    reason: str = "",
    actor_role: str = "",
) -> bool:
    """
    Insert an audit row. Returns False if idempotency_key already exists
    (meaning: this exact action was already performed — do not repeat it).
    `reason` is the human request that triggered the action (the "why" admins see in the logs).
    """
    try:
        await session.execute(
            audit_log.insert().values(
                action=action,
                employee_id=employee_id,
                requested_by=requested_by,
                idempotency_key=idempotency_key,
                status=status,
                detail=(detail or "")[:500],
                reason=(reason or "")[:500],
                actor_role=actor_role,
                source="agent",
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
    reason: str = "",
) -> dict:
    decision = check_permission("create_ticket", employee_id, requested_by, requester_role)

    async with session_factory() as session:
        if not decision.allowed:
            await _write_audit(
                session, "create_ticket", employee_id, requested_by,
                idempotency_key, "denied", decision.reason, reason, requester_role,
            )
            return {"success": False, "reason": decision.reason}

        wrote = await _write_audit(
            session, "create_ticket", employee_id, requested_by,
            idempotency_key, "success", f"category={category}", reason, requester_role,
        )
        if not wrote:
            return {"success": False, "reason": "duplicate_request (already processed)"}

        ticket_id = f"T{int(datetime.now(timezone.utc).timestamp())}"
        now = iso()
        await session.execute(
            tickets.insert().values(
                ticket_id=ticket_id,
                employee_id=employee_id,
                title=category,
                description=description,
                status="open",
                category=category,
                priority="Medium",
                created_at=now,
                updated_at=now,
            )
        )
        await session.execute(
            ticket_events.insert().values(
                ticket_id=ticket_id, event_type="created", to_value="open",
                performed_by=requested_by, note="Ticket created through the assistant", created_at=now,
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
    reason: str = "",
) -> dict:
    """
    Step 1 of a reset (this tool, after human approval): authorise it and open a short-lived reset window.
    Step 2: the employee types the new password into the portal's 'Set new password' box.
    The password never passes through chat or voice, and is only ever stored hashed.
    """
    decision = check_permission("reset_password", employee_id, requested_by, requester_role)

    async with session_factory() as session:
        if not decision.allowed:
            await _write_audit(
                session, "reset_password", employee_id, requested_by,
                idempotency_key, "denied", decision.reason, reason, requester_role,
            )
            return {"success": False, "reason": decision.reason}

        exists = (await session.execute(
            select(employees.c.employee_id).where(employees.c.employee_id == employee_id))).first()
        if not exists:
            return {"success": False, "reason": f"employee_not_found: {employee_id}"}

        wrote = await _write_audit(
            session, "reset_password", employee_id, requested_by,
            idempotency_key, "success", f"password reset approved; valid {RESET_TTL_MIN} min", reason, requester_role,
        )
        if not wrote:
            return {"success": False, "reason": "duplicate_request (already processed)"}

        info = await create_password_reset(session, employee_id, requested_by)
        await session.commit()

    who = "you" if employee_id == requested_by else employee_id
    return {
        "success": True,
        "detail": (
            f"Password reset approved for {employee_id}. {who.capitalize() if who == 'you' else who} must now enter the new "
            f"password in the 'Set new password' box on the OpsPilot page (valid for {info['minutes']} minutes). "
            "Do NOT ask for the password in chat or voice."
        ),
        "expires_at": info["expires_at"],
    }


async def revoke_access(
    session_factory: async_sessionmaker,
    employee_id: str,
    requested_by: str,
    requester_role: str,
    idempotency_key: str,
    reason: str = "",
) -> dict:
    # Deliberately stricter: only admins may revoke access (no self-service).
    if requester_role != "admin":
        decision_reason = f"scope_violation: '{requested_by}' (role={requester_role}) cannot revoke access"
        async with session_factory() as session:
            await _write_audit(
                session, "revoke_access", employee_id, requested_by,
                idempotency_key, "denied", decision_reason, reason, requester_role,
            )
        return {"success": False, "reason": decision_reason}

    async with session_factory() as session:
        wrote = await _write_audit(
            session, "revoke_access", employee_id, requested_by,
            idempotency_key, "success", "access revoked", reason, requester_role,
        )
        if not wrote:
            return {"success": False, "reason": "duplicate_request (already processed)"}

        await session.execute(
            update(employees)
            .where(employees.c.employee_id == employee_id)
            .values(account_locked=1)
        )
        await record_revoke_lock(session, employee_id, requested_by, reason or "access revoked")
        await session.commit()
        return {"success": True, "detail": f"Access revoked for {employee_id}. They can no longer log in until an admin unlocks the account."}
