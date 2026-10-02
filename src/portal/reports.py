"""Read models for the portal: admin overview, analytics, employee 360, activity logs, tickets."""
from collections import Counter, defaultdict
from datetime import timedelta

from sqlalchemy import select, update, func, desc, or_

from src.mcp_servers.ops.models import audit_log
from src.mcp_servers.sql.models import (
    employees, tickets, ticket_events, users, login_events, account_lock_events,
    projects, project_assignments, project_updates, departments,
)
from src.portal import accounts, projects as proj
from src.portal.common import ServiceError, add_audit, employee_names, iso, now_dt, one, parse_ts, rows

TICKET_STATUSES = ("open", "in_progress", "resolved", "closed")
_OPEN = ("open", "in_progress")


def _norm(v) -> str:
    return str(v or "").strip().lower()


def _counts(values) -> list[dict]:
    return [{"name": k or "n/a", "count": c} for k, c in Counter(values).most_common()]


def _since(days: int) -> str:
    return iso(now_dt() - timedelta(days=days))


# ------------------------------------------------------------------ tickets ---------------------
async def list_tickets(session_factory, *, viewer_id: str, role: str, status: str | None = None, priority: str | None = None,
                       category: str | None = None, employee_id: str | None = None, ticket_id: str | None = None,
                       limit: int = 200) -> list[dict]:
    async with session_factory() as s:
        q = (select(tickets, employees.c.name.label("employee_name"), employees.c.department)
             .select_from(tickets.outerjoin(employees, tickets.c.employee_id == employees.c.employee_id)))
        if role != "admin":
            q = q.where(tickets.c.employee_id == viewer_id)
        elif employee_id:
            q = q.where(or_(tickets.c.employee_id == employee_id, tickets.c.assigned_to == employee_id))
        if status:
            q = q.where(func.lower(tickets.c.status) == status.lower())
        if ticket_id:
            q = q.where(func.upper(tickets.c.ticket_id) == ticket_id.upper())
        if priority:
            q = q.where(func.lower(tickets.c.priority) == priority.lower())
        if category:
            q = q.where(func.lower(tickets.c.category) == category.lower())
        out = rows(await s.execute(q.order_by(desc(tickets.c.created_at), desc(tickets.c.ticket_id)).limit(min(limit, 500))))
        names = await employee_names(s, {t["assigned_to"] for t in out})
    for t in out:
        t["status"] = _norm(t["status"])
        t["assigned_name"] = names.get(t["assigned_to"])
    return out


async def update_ticket(session_factory, *, ticket_id: str, performed_by: str, actor_role: str, status: str | None = None,
                        assigned_to: str | None = None, priority: str | None = None, note: str = "",
                        source: str = "portal") -> dict:
    if actor_role != "admin":
        raise ServiceError("Only an admin can change tickets.", 403)
    async with session_factory() as s:
        t = one(await s.execute(select(tickets).where(func.upper(tickets.c.ticket_id) == ticket_id.upper())))
        if not t:
            raise ServiceError(f"Ticket {ticket_id} not found.", 404)
        now, values, events = iso(), {"updated_at": iso()}, []
        if status:
            status = _norm(status).replace(" ", "_")
            if status not in TICKET_STATUSES:
                raise ServiceError("Status must be one of: open, in_progress, resolved, closed.", 400)
            if status != _norm(t["status"]):
                values["status"] = status
                values["resolved_at"] = now if status in ("resolved", "closed") else None
                events.append(("status_change", _norm(t["status"]), status))
        if assigned_to:
            who = await proj.resolve_employee(s, assigned_to)
            if who["employee_id"] != t["assigned_to"]:
                values["assigned_to"] = who["employee_id"]
                events.append(("assigned", t["assigned_to"], who["employee_id"]))
        if priority:
            pr = priority.strip().capitalize()
            if pr not in ("Low", "Medium", "High", "Critical"):
                raise ServiceError("Priority must be one of: Low, Medium, High, Critical.", 400)
            if pr != t["priority"]:
                values["priority"] = pr
                events.append(("priority_change", t["priority"], pr))
        if not events and not note:
            raise ServiceError("Nothing to change.", 400)
        await s.execute(update(tickets).where(tickets.c.ticket_id == t["ticket_id"]).values(**values))
        for kind, a, b in events or [("comment", None, None)]:
            await s.execute(ticket_events.insert().values(
                ticket_id=t["ticket_id"], event_type=kind, from_value=a, to_value=b, performed_by=performed_by,
                note=note or None, created_at=now))
        await add_audit(s, action="update_ticket", employee_id=t["employee_id"], requested_by=performed_by, status="success",
                        detail=f"{t['ticket_id']}: " + ", ".join(f"{k} {a}->{b}" for k, a, b in events),
                        reason=note or "Ticket updated from the portal", actor_role=actor_role, source=source)
        await s.commit()
    return (await list_tickets(session_factory, viewer_id=performed_by, role="admin", ticket_id=t["ticket_id"]))[0]


async def ticket_history(session_factory, ticket_id: str) -> list[dict]:
    async with session_factory() as s:
        ev = rows(await s.execute(select(ticket_events).where(ticket_events.c.ticket_id == ticket_id).order_by(ticket_events.c.event_id)))
        names = await employee_names(s, {e["performed_by"] for e in ev})
    for e in ev:
        e["performed_by_name"] = names.get(e["performed_by"])
    return ev


# ------------------------------------------------------------------ activity logs ---------------
async def list_audit(session_factory, *, employee_id: str | None = None, action: str | None = None, status: str | None = None,
                     source: str | None = None, days: int | None = None, limit: int = 200, offset: int = 0) -> list[dict]:
    """What Ops did, to whom, why, and when. `employee_id` matches either the target or the requester."""
    async with session_factory() as s:
        q = select(audit_log)
        if employee_id:
            q = q.where(or_(audit_log.c.employee_id == employee_id, audit_log.c.requested_by == employee_id))
        if action:
            q = q.where(audit_log.c.action == action)
        if status:
            q = q.where(audit_log.c.status == status)
        if source:
            q = q.where(audit_log.c.source == source)
        if days:
            q = q.where(audit_log.c.created_at >= (now_dt() - timedelta(days=int(days))).replace(tzinfo=None))
        out = rows(await s.execute(q.order_by(desc(audit_log.c.id)).limit(min(limit, 500)).offset(max(offset, 0))))
        names = await employee_names(s, {r["employee_id"] for r in out} | {r["requested_by"] for r in out})
    for r in out:
        r["target_name"] = names.get(r["employee_id"])
        r["actor_name"] = names.get(r["requested_by"]) or r["requested_by"]
        r["source"] = r["source"] or "agent"
    return out


async def audit_actions(session_factory) -> list[str]:
    async with session_factory() as s:
        return [r[0] for r in (await s.execute(select(audit_log.c.action).distinct().order_by(audit_log.c.action))).all()]


async def list_login_events(session_factory, *, only_failed: bool = False, employee_id: str | None = None, limit: int = 100) -> list[dict]:
    async with session_factory() as s:
        q = select(login_events)
        if only_failed:
            q = q.where(login_events.c.success == 0)
        if employee_id:
            q = q.where(login_events.c.employee_id == employee_id)
        out = rows(await s.execute(q.order_by(desc(login_events.c.event_id)).limit(min(limit, 500))))
        names = await employee_names(s, {r["employee_id"] for r in out})
    for r in out:
        r["name"] = names.get(r["employee_id"])
    return out


# ------------------------------------------------------------------ employees -------------------
async def list_employees(session_factory) -> list[dict]:
    async with session_factory() as s:
        emps = rows(await s.execute(select(employees).order_by(employees.c.employee_id)))
        tk = rows(await s.execute(select(tickets.c.employee_id, tickets.c.status)))
        asg = rows(await s.execute(select(project_assignments.c.employee_id, project_assignments.c.status)
                                   .where(project_assignments.c.status != "removed")))
        last_act = dict((r[0], r[1]) for r in (await s.execute(
            select(audit_log.c.requested_by, func.max(audit_log.c.created_at)).group_by(audit_log.c.requested_by))).all())
    accts = {a["employee_id"]: a for a in await accounts.list_accounts(session_factory)}
    open_t, all_t, proj_n = Counter(), Counter(), Counter()
    for t in tk:
        all_t[t["employee_id"]] += 1
        if _norm(t["status"]) in _OPEN:
            open_t[t["employee_id"]] += 1
    for a in asg:
        proj_n[a["employee_id"]] += 1
    for e in emps:
        a = accts.get(e["employee_id"], {})
        e.update(open_tickets=open_t[e["employee_id"]], total_tickets=all_t[e["employee_id"]], projects=proj_n[e["employee_id"]],
                 role=a.get("role"), account_state=a.get("state", "no_login"), last_login_at=a.get("last_login_at"),
                 last_activity=str(last_act.get(e["employee_id"]))[:19] if last_act.get(e["employee_id"]) else None)
    return emps


async def employee_profile(session_factory, employee_id: str) -> dict:
    """Everything about one person: profile, account, projects, tickets, what Ops did to / for them, logins, locks."""
    async with session_factory() as s:
        emp = one(await s.execute(select(employees).where(employees.c.employee_id == employee_id)))
        if not emp:
            raise ServiceError(f"Employee {employee_id} not found.", 404)
        names = await employee_names(s, {emp["manager_id"]})
        owned = rows(await s.execute(select(tickets).where(tickets.c.employee_id == employee_id).order_by(desc(tickets.c.created_at))))
        assigned = rows(await s.execute(select(tickets).where(tickets.c.assigned_to == employee_id).order_by(desc(tickets.c.created_at))))
        logins = rows(await s.execute(select(login_events).where(login_events.c.employee_id == employee_id)
                                      .order_by(desc(login_events.c.event_id)).limit(20)))
        locks = rows(await s.execute(select(account_lock_events).where(account_lock_events.c.employee_id == employee_id)
                                     .order_by(desc(account_lock_events.c.event_id)).limit(20)))
        updates = rows(await s.execute(select(project_updates).where(project_updates.c.employee_id == employee_id)
                                       .order_by(desc(project_updates.c.update_id)).limit(20)))
    for t in owned + assigned:
        t["status"] = _norm(t["status"])
    acct = next((a for a in await accounts.list_accounts(session_factory) if a["employee_id"] == employee_id), None)
    emp["manager_name"] = names.get(emp["manager_id"])
    audits = await list_audit(session_factory, employee_id=employee_id, limit=300)
    return {
        "profile": emp, "account": acct,
        "projects": await proj.list_projects(session_factory, employee_id, "employee"),
        "tickets_raised": owned, "tickets_assigned": assigned,
        "ops_actions_on_employee": [r for r in audits if r["employee_id"] == employee_id][:60],
        "actions_requested_by_employee": [r for r in audits if r["requested_by"] == employee_id][:60],
        "logins": logins, "lock_events": locks, "project_updates": updates,
        "stats": {
            "open_tickets": sum(1 for t in owned if t["status"] in _OPEN), "total_tickets": len(owned),
            "assigned_open": sum(1 for t in assigned if t["status"] in _OPEN),
            "failed_logins_14d": sum(1 for l in logins if not l["success"] and (l["created_at"] or "") >= _since(14)),
        },
    }


# ------------------------------------------------------------------ employee home --------------
async def my_overview(session_factory, employee_id: str, role: str) -> dict:
    async with session_factory() as s:
        emp = one(await s.execute(select(employees).where(employees.c.employee_id == employee_id)))
    mine = await proj.list_projects(session_factory, employee_id, "employee")
    tk = await list_tickets(session_factory, viewer_id=employee_id, role="employee", limit=100)
    return {
        "profile": emp or {"employee_id": employee_id, "name": employee_id},
        "role": role, "projects": mine, "tickets": tk,
        "pending_password_reset": await accounts.get_pending_reset(session_factory, employee_id),
        "stats": {"projects": len(mine), "open_tickets": sum(1 for t in tk if t["status"] in _OPEN),
                  "blocked": sum(1 for p in mine if p["my_state"] == "blocked")},
    }


# ------------------------------------------------------------------ admin overview + analytics --
async def admin_overview(session_factory) -> dict:
    async with session_factory() as s:
        emps = rows(await s.execute(select(employees.c.employee_id)))
        tk = rows(await s.execute(select(tickets.c.status, tickets.c.priority, tickets.c.assigned_to)))
        pr = rows(await s.execute(select(projects.c.stage, projects.c.status, projects.c.due_date)))
        fails = (await s.execute(select(func.count()).select_from(login_events).where(
            login_events.c.success == 0, login_events.c.created_at >= _since(1)))).scalar()
        actions = (await s.execute(select(func.count()).select_from(audit_log).where(
            audit_log.c.created_at >= (now_dt() - timedelta(days=1)).replace(tzinfo=None)))).scalar()
    accts = await accounts.list_accounts(session_factory)
    open_t = [t for t in tk if _norm(t["status"]) in _OPEN]
    today = iso()[:10]
    blocked = 0
    live = await proj.list_projects(session_factory, "", "admin", status="active")
    blocked = sum(p["blocked_count"] for p in live)
    return {
        "employees": len(emps), "accounts_locked": sum(1 for a in accts if a["state"] != "active"),
        "open_tickets": len(open_t), "critical_open": sum(1 for t in open_t if _norm(t["priority"]) == "critical"),
        "unassigned_open": sum(1 for t in open_t if not t["assigned_to"]),
        "active_projects": sum(1 for p in pr if p["status"] == "active"),
        "overdue_projects": sum(1 for p in pr if p["status"] == "active" and p["due_date"] and p["due_date"] < today),
        "blocked_people": blocked, "failed_logins_24h": fails, "ops_actions_24h": actions,
        "projects_by_stage": _counts([p["stage"] for p in pr]),
    }


async def analytics(session_factory) -> dict:
    now = now_dt()
    async with session_factory() as s:
        tk = rows(await s.execute(
            select(tickets, employees.c.department.label("dept"), employees.c.name.label("requester"))
            .select_from(tickets.outerjoin(employees, tickets.c.employee_id == employees.c.employee_id))))
        logins = rows(await s.execute(select(login_events).where(login_events.c.created_at >= _since(14))))
        locks = rows(await s.execute(select(account_lock_events).where(account_lock_events.c.created_at >= _since(30))))
        audit = rows(await s.execute(select(audit_log.c.action, audit_log.c.status, audit_log.c.source, audit_log.c.created_at)
                                     .where(audit_log.c.created_at >= (now - timedelta(days=30)).replace(tzinfo=None))))
        pr = rows(await s.execute(select(projects)))
        asg = rows(await s.execute(select(project_assignments).where(project_assignments.c.status != "removed")))
        emps = rows(await s.execute(select(employees.c.employee_id, employees.c.name, employees.c.department)))
    for t in tk:
        t["status"], t["priority"] = _norm(t["status"]), t["priority"] or "n/a"

    hours = defaultdict(list)
    for t in tk:
        a, b = parse_ts(t["created_at"]), parse_ts(t["resolved_at"])
        if a and b and t["status"] in ("resolved", "closed"):
            hours[t["category"] or "n/a"].append((b - a).total_seconds() / 3600)
    all_h = [h for v in hours.values() for h in v]

    by_dept = defaultdict(lambda: {"tickets": 0, "open": 0})
    for t in tk:
        d = by_dept[t["dept"] or "n/a"]
        d["tickets"] += 1
        d["open"] += t["status"] in _OPEN

    days = [(now - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(13, -1, -1)]
    created, solved = Counter(), Counter()
    for t in tk:
        created[(t["created_at"] or "")[:10]] += 1
        if t["resolved_at"]:
            solved[t["resolved_at"][:10]] += 1
    ok, bad = Counter(), Counter()
    for l in logins:
        (ok if l["success"] else bad)[(l["created_at"] or "")[:10]] += 1

    load = defaultdict(lambda: {"projects": 0, "open_tickets": 0, "assigned_tickets": 0})
    for a in asg:
        load[a["employee_id"]]["projects"] += 1
    for t in tk:
        if t["status"] in _OPEN:
            load[t["employee_id"]]["open_tickets"] += 1
            if t["assigned_to"]:
                load[t["assigned_to"]]["assigned_tickets"] += 1
    ename = {e["employee_id"]: e for e in emps}
    workload = sorted(
        ({"employee_id": k, "name": ename.get(k, {}).get("name", k), "department": ename.get(k, {}).get("department"), **v}
         for k, v in load.items()), key=lambda r: -(r["projects"] + r["open_tickets"] + r["assigned_tickets"]))[:12]

    today = iso()[:10]
    return {
        "tickets": {
            "total": len(tk), "by_status": _counts(t["status"] for t in tk), "by_priority": _counts(t["priority"] for t in tk),
            "by_category": _counts(t["category"] for t in tk),
            "by_department": sorted(({"name": k, **v} for k, v in by_dept.items()), key=lambda r: -r["tickets"]),
            "avg_resolution_hours": round(sum(all_h) / len(all_h), 1) if all_h else None,
            "resolution_by_category": sorted(({"name": k, "avg_hours": round(sum(v) / len(v), 1), "resolved": len(v)}
                                              for k, v in hours.items()), key=lambda r: r["avg_hours"]),
            "per_day": [{"day": d, "created": created[d], "resolved": solved[d]} for d in days],
            "top_requesters": _counts(t["requester"] for t in tk)[:6],
            "oldest_open": sorted((t for t in tk if t["status"] in _OPEN and t["created_at"]), key=lambda t: t["created_at"])[:6],
        },
        "projects": {
            "by_stage": _counts(p["stage"] for p in pr), "by_priority": _counts(p["priority"] for p in pr),
            "overdue": [{"project_id": p["project_id"], "name": p["name"], "due_date": p["due_date"], "progress_pct": p["progress_pct"]}
                        for p in pr if p["status"] == "active" and p["due_date"] and p["due_date"] < today],
            "avg_progress": round(sum(p["progress_pct"] for p in pr if p["status"] == "active") /
                                  max(1, sum(1 for p in pr if p["status"] == "active"))),
        },
        "workload": workload,
        "security": {
            "logins_per_day": [{"day": d, "success": ok[d], "failed": bad[d]} for d in days],
            "top_failed": _counts(l["username"] for l in logins if not l["success"])[:6],
            "lock_events_30d": _counts(f"{e['lock_type']}:{e['action']}" for e in locks),
        },
        "ops_actions": {
            "by_action": _counts(f"{a['action']} ({a['status']})" for a in audit)[:12],
            "by_source": _counts((a["source"] or "agent") for a in audit),
        },
    }
