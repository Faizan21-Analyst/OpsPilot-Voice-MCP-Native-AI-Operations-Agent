"""Login, advanced account locking, and the approved-password-reset flow."""
import asyncio
import math
import re
from datetime import timedelta

from sqlalchemy import select, update, func

from src.auth.security import hash_password, verify_password
from src.auth.users import get_user
from src.mcp_servers.sql.models import (
    employees, users, password_resets, login_events, account_lock_events,
)
from src.portal.common import (
    ServiceError, add_audit, iso, now_dt, one, parse_ts, rows,
)

MAX_ATTEMPTS = 5
LOCK_MINUTES = (15, 30, 60)   # escalates with every automatic lockout; resets on a successful login
RESET_TTL_MIN = 30


async def _safe_verify(plain: str, hashed: str) -> bool:
    try:
        return await asyncio.to_thread(verify_password, plain, hashed)
    except Exception:
        return False


def _event(session, username, employee_id, success, reason, ip):
    return session.execute(login_events.insert().values(
        username=username, employee_id=employee_id, success=1 if success else 0,
        reason=reason, ip=ip, created_at=iso()))


async def authenticate(session_factory, username: str, password: str, ip: str | None = None) -> dict:
    """Returns {"user_id", "role"} or raises ServiceError (401 bad credentials, 423 locked)."""
    uname = (username or "").strip().lower()
    async with session_factory() as s:
        user = one(await s.execute(select(users).where(func.lower(users.c.username) == uname)))

        if user is None:
            legacy = get_user(uname)  # original in-memory demo users, kept for backwards compatibility
            if legacy and await _safe_verify(password, legacy.password_hash):
                return {"user_id": legacy.user_id, "role": legacy.role}
            await _event(s, uname, None, False, "unknown_user", ip)
            await s.commit()
            raise ServiceError("Invalid username or password", 401)

        eid = user["employee_id"]
        emp = one(await s.execute(select(employees).where(employees.c.employee_id == eid)))
        now = now_dt()

        if emp and emp.get("account_locked"):
            await _event(s, uname, eid, False, "access_revoked", ip)
            await s.commit()
            raise ServiceError("This account's access has been revoked by an administrator.", 423)
        if user["manually_locked"]:
            await _event(s, uname, eid, False, "manually_locked", ip)
            await s.commit()
            why = f" Reason: {user['lock_reason']}" if user.get("lock_reason") else ""
            raise ServiceError(f"This account is locked by an administrator.{why}", 423)
        until = parse_ts(user["locked_until"])
        if until and until > now:
            mins = max(1, math.ceil((until - now).total_seconds() / 60))
            await _event(s, uname, eid, False, "temporarily_locked", ip)
            await s.commit()
            if user.get("lock_reason"):
                raise ServiceError(f"This account is locked by an administrator for {mins} more minute(s). Reason: {user['lock_reason']}", 423)
            raise ServiceError(f"Too many failed attempts. Try again in {mins} minute(s).", 423)

        if await _safe_verify(password, user["password_hash"]):
            await s.execute(update(users).where(users.c.user_id == user["user_id"]).values(
                failed_attempts=0, lock_count=0, locked_until=None, last_login_at=iso(now)))
            await _event(s, uname, eid, True, "ok", ip)
            await s.commit()
            return {"user_id": eid, "role": user["role"]}

        attempts = (user["failed_attempts"] or 0) + 1
        values = {"failed_attempts": attempts}
        message, status = f"Invalid username or password. {MAX_ATTEMPTS - attempts} attempt(s) left before lockout.", 401
        if attempts >= MAX_ATTEMPTS:
            lock_count = (user["lock_count"] or 0) + 1
            minutes = LOCK_MINUTES[min(lock_count, len(LOCK_MINUTES)) - 1]
            until_s = iso(now + timedelta(minutes=minutes))
            values.update(failed_attempts=0, lock_count=lock_count, locked_until=until_s)
            reason = f"{MAX_ATTEMPTS} consecutive failed logins (lockout #{lock_count})"
            await s.execute(account_lock_events.insert().values(
                employee_id=eid, action="locked", lock_type="auto", reason=reason,
                performed_by="system", locked_until=until_s, created_at=iso(now)))
            await add_audit(s, action="account_locked", employee_id=eid, requested_by="system", status="success",
                            detail=f"locked until {until_s} UTC", reason=reason, actor_role="system", source="login")
            message, status = f"Too many failed attempts. Account locked for {minutes} minutes.", 423
        await s.execute(update(users).where(users.c.user_id == user["user_id"]).values(**values))
        await _event(s, uname, eid, False, "bad_password", ip)
        await s.commit()
        raise ServiceError(message, status)


async def lock_account(session_factory, employee_id: str, reason: str, performed_by: str, minutes: int | None = None) -> dict:
    reason = (reason or "").strip()
    if not reason:
        raise ServiceError("A reason is required to lock an account.", 400)
    if employee_id == performed_by:
        raise ServiceError("You cannot lock your own account.", 400)
    async with session_factory() as s:
        user = one(await s.execute(select(users).where(users.c.employee_id == employee_id)))
        if not user:
            raise ServiceError(f"No login account exists for {employee_id}.", 404)
        until_s = None
        if minutes:
            until_s = iso(now_dt() + timedelta(minutes=int(minutes)))
            values = dict(locked_until=until_s, lock_reason=reason)
        else:
            values = dict(manually_locked=1, lock_reason=reason)
        await s.execute(update(users).where(users.c.employee_id == employee_id).values(**values))
        await s.execute(account_lock_events.insert().values(
            employee_id=employee_id, action="locked", lock_type="manual", reason=reason,
            performed_by=performed_by, locked_until=until_s, created_at=iso()))
        await add_audit(s, action="account_locked", employee_id=employee_id, requested_by=performed_by,
                        status="success", detail=f"manual lock{' until ' + until_s if until_s else ' (until unlocked)'}",
                        reason=reason, actor_role="admin", source="portal")
        await s.commit()
    return {"employee_id": employee_id, "locked": True, "locked_until": until_s}


async def unlock_account(session_factory, employee_id: str, reason: str, performed_by: str) -> dict:
    async with session_factory() as s:
        user = one(await s.execute(select(users).where(users.c.employee_id == employee_id)))
        if not user:
            raise ServiceError(f"No login account exists for {employee_id}.", 404)
        await s.execute(update(users).where(users.c.employee_id == employee_id).values(
            manually_locked=0, locked_until=None, failed_attempts=0, lock_count=0, lock_reason=None))
        await s.execute(update(employees).where(employees.c.employee_id == employee_id).values(account_locked=0))
        await s.execute(account_lock_events.insert().values(
            employee_id=employee_id, action="unlocked", lock_type="manual", reason=reason or "unlocked by admin",
            performed_by=performed_by, created_at=iso()))
        await add_audit(s, action="account_unlocked", employee_id=employee_id, requested_by=performed_by,
                        status="success", detail="all lock flags cleared", reason=reason or "unlocked by admin",
                        actor_role="admin", source="portal")
        await s.commit()
    return {"employee_id": employee_id, "locked": False}


async def record_revoke_lock(session, employee_id: str, performed_by: str, reason: str) -> None:
    """Called by the ops revoke_access tool (same transaction as its audit row)."""
    await session.execute(account_lock_events.insert().values(
        employee_id=employee_id, action="locked", lock_type="revoked", reason=reason or "access revoked",
        performed_by=performed_by, created_at=iso()))


# ---------------------------------------------------------------- password reset ----------------
def check_password_policy(pw: str) -> None:
    if not pw or len(pw) < 8:
        raise ServiceError("Password must be at least 8 characters long.", 400)
    if len(pw.encode()) > 72:
        raise ServiceError("Password is too long (max 72 bytes).", 400)
    if not re.search(r"[A-Za-z]", pw) or not re.search(r"\d", pw):
        raise ServiceError("Password must contain at least one letter and one number.", 400)
    if pw.lower() in {"password1", "password123", "12345678", "qwerty123", "welcome@123"}:
        raise ServiceError("That password is too common. Choose another one.", 400)


async def create_password_reset(session, employee_id: str, requested_by: str) -> dict:
    """Called by the ops reset_password tool once the human approved. Does not commit."""
    await session.execute(update(password_resets).where(
        password_resets.c.employee_id == employee_id, password_resets.c.status == "pending"
    ).values(status="cancelled"))
    now = now_dt()
    expires = iso(now + timedelta(minutes=RESET_TTL_MIN))
    await session.execute(password_resets.insert().values(
        employee_id=employee_id, requested_by=requested_by, status="pending",
        created_at=iso(now), expires_at=expires))
    return {"expires_at": expires, "minutes": RESET_TTL_MIN}


async def get_pending_reset(session_factory, employee_id: str) -> dict | None:
    async with session_factory() as s:
        row = one(await s.execute(select(password_resets).where(
            password_resets.c.employee_id == employee_id, password_resets.c.status == "pending"
        ).order_by(password_resets.c.reset_id.desc())))
        if not row:
            return None
        exp = parse_ts(row["expires_at"])
        if exp and exp < now_dt():
            await s.execute(update(password_resets).where(password_resets.c.reset_id == row["reset_id"]).values(status="expired"))
            await s.commit()
            return None
        return {"reset_id": row["reset_id"], "requested_by": row["requested_by"], "expires_at": row["expires_at"]}


async def complete_password_reset(session_factory, employee_id: str, new_password: str) -> dict:
    check_password_policy(new_password)
    pending = await get_pending_reset(session_factory, employee_id)
    if not pending:
        raise ServiceError("There is no approved password reset waiting for you (it may have expired).", 403)
    new_hash = await asyncio.to_thread(hash_password, new_password)
    async with session_factory() as s:
        user = one(await s.execute(select(users).where(users.c.employee_id == employee_id)))
        now = iso()
        if user:
            if await _safe_verify(new_password, user["password_hash"]):
                raise ServiceError("Choose a password you are not currently using.", 400)
            await s.execute(update(users).where(users.c.employee_id == employee_id).values(
                password_hash=new_hash, password_changed_at=now, failed_attempts=0, lock_count=0,
                locked_until=None))
        else:
            await s.execute(users.insert().values(
                username=employee_id.lower(), employee_id=employee_id, password_hash=new_hash,
                role="employee", created_at=now, password_changed_at=now))
        await s.execute(update(password_resets).where(password_resets.c.reset_id == pending["reset_id"]).values(
            status="used", used_at=now))
        await add_audit(s, action="password_set", employee_id=employee_id, requested_by=employee_id,
                        status="success", detail="new password stored (hashed)",
                        reason="Employee set a new password after an approved reset",
                        actor_role="employee", source="portal")
        await s.commit()
    return {"success": True}


async def list_accounts(session_factory) -> list[dict]:
    """Security overview: one row per login account with its lock state."""
    async with session_factory() as s:
        res = await s.execute(
            select(users.c.username, users.c.employee_id, users.c.role, users.c.failed_attempts, users.c.lock_count,
                   users.c.locked_until, users.c.manually_locked, users.c.lock_reason, users.c.last_login_at,
                   users.c.password_changed_at, employees.c.name, employees.c.department, employees.c.account_locked)
            .select_from(users.outerjoin(employees, users.c.employee_id == employees.c.employee_id))
            .order_by(users.c.employee_id))
        out = rows(res)
    now = now_dt()
    for r in out:
        until = parse_ts(r["locked_until"])
        r["temp_locked"] = bool(until and until > now)
        r["state"] = ("revoked" if r.get("account_locked") else "locked" if r["manually_locked"]
                      else "temp_locked" if r["temp_locked"] else "active")
    return out
