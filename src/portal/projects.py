"""Projects: create, assign, employee progress updates, stage control and the flow (pipeline) view."""
import re
from datetime import datetime

from sqlalchemy import select, update, func, desc

from src.mcp_servers.sql.models import employees, projects, project_assignments, project_updates
from src.portal.common import ServiceError, add_audit, employee_names, iso, one, rows

STAGES = ["planning", "in_progress", "review", "done"]
STAGE_LABEL = {"planning": "Planning", "in_progress": "In Progress", "review": "Review", "done": "Done"}
PRIORITIES = ("low", "medium", "high", "critical")
_TYPE_ALIASES = {
    "progress": "progress", "update": "progress", "working": "progress", "started": "progress",
    "in_progress": "progress", "in progress": "progress",
    "blocked": "blocked", "stuck": "blocked",
    "done": "done", "complete": "done", "completed": "done", "finished": "done", "finish": "done",
    "note": "note", "comment": "note",
}


def _require_admin(role: str) -> None:
    if role != "admin":
        raise ServiceError("Only an admin can do this.", 403)


def _norm_type(value: str) -> str:
    key = (value or "").strip().lower().replace("-", "_")
    if key in _TYPE_ALIASES:
        return _TYPE_ALIASES[key]
    for word, kind in (("finish", "done"), ("complet", "done"), ("done", "done"), ("block", "blocked"),
                       ("stuck", "blocked"), ("progress", "progress"), ("work", "progress"), ("start", "progress")):
        if word in key:   # the assistant may pass free-form words such as "I'm finished"
            return kind
    raise ServiceError("Update type must be one of: progress, blocked, done, note.", 400)


def _norm_date(value):
    if not value:
        return None
    try:
        datetime.strptime(str(value)[:10], "%Y-%m-%d")
    except ValueError:
        raise ServiceError("Due date must look like YYYY-MM-DD.", 400)
    return str(value)[:10]


# ------------------------------------------------------------------ lookups --------------------
async def resolve_employee(session, ref: str) -> dict:
    ref = (ref or "").strip()
    if not ref:
        raise ServiceError("An employee is required.", 400)
    emp = one(await session.execute(select(employees).where(func.upper(employees.c.employee_id) == ref.upper())))
    if emp:
        return emp
    found = rows(await session.execute(select(employees).where(employees.c.name.ilike(f"%{ref}%")).limit(6)))
    if len(found) == 1:
        return found[0]
    if not found:
        raise ServiceError(f"No employee found matching '{ref}'.", 404)
    options = ", ".join(f"{f['name']} ({f['employee_id']})" for f in found)
    raise ServiceError(f"Several employees match '{ref}': {options}. Please use the employee ID.", 409)


async def resolve_project(session, ref: str) -> dict:
    ref = (ref or "").strip()
    if not ref:
        raise ServiceError("A project is required.", 400)
    p = one(await session.execute(select(projects).where(func.upper(projects.c.project_id) == ref.upper())))
    if p:
        return p
    found = rows(await session.execute(select(projects).where(projects.c.name.ilike(f"%{ref}%")).limit(6)))
    if len(found) == 1:
        return found[0]
    if not found:
        raise ServiceError(f"No project found matching '{ref}'.", 404)
    options = ", ".join(f"{f['name']} ({f['project_id']})" for f in found)
    raise ServiceError(f"Several projects match '{ref}': {options}. Please use the project ID.", 409)


async def _next_project_id(session) -> str:
    ids = (await session.execute(select(projects.c.project_id))).scalars().all()
    nums = [int(m.group(1)) for i in ids if (m := re.fullmatch(r"P(\d+)", i))]
    return f"P{(max(nums) + 1 if nums else 1):03d}"


# ------------------------------------------------------------------ team state ------------------
async def _members(session, pids: list[str]) -> dict[str, list[dict]]:
    """For each project: its (non-removed) team with each person's current state and % complete."""
    if not pids:
        return {}
    team = rows(await session.execute(
        select(project_assignments, employees.c.name, employees.c.job_title, employees.c.department)
        .select_from(project_assignments.outerjoin(employees, project_assignments.c.employee_id == employees.c.employee_id))
        .where(project_assignments.c.project_id.in_(pids), project_assignments.c.status != "removed")
        .order_by(project_assignments.c.assignment_id)))
    ups = rows(await session.execute(
        select(project_updates).where(project_updates.c.project_id.in_(pids), project_updates.c.employee_id.isnot(None),
                                      project_updates.c.update_type.in_(("progress", "blocked", "done", "note")))
        .order_by(project_updates.c.update_id)))
    last, pct = {}, {}
    for u in ups:
        key = (u["project_id"], u["employee_id"])
        last[key] = u
        if u["progress_pct"] is not None and u["update_type"] != "note":
            pct[key] = u["progress_pct"]
    out: dict[str, list[dict]] = {pid: [] for pid in pids}
    for t in team:
        key = (t["project_id"], t["employee_id"])
        lu = last.get(key)
        if t["status"] == "done":
            state, p = "done", 100
        else:
            p = pct.get(key, 0)
            state = "blocked" if lu and lu["update_type"] == "blocked" else ("in_progress" if key in pct or (lu and lu["update_type"] == "progress") else "not_started")
        out[t["project_id"]].append({
            "employee_id": t["employee_id"], "name": t["name"], "job_title": t["job_title"], "department": t["department"],
            "role_in_project": t["role_in_project"], "assignment_status": t["status"], "state": state, "pct": p,
            "last_message": lu["message"] if lu else None, "last_at": lu["created_at"] if lu else None,
            "assigned_at": t["assigned_at"],
        })
    return out


async def _recompute(session, project_id: str, actor: str | None) -> dict:
    """Recalculate progress + auto-advance the stage. Does not commit."""
    p = one(await session.execute(select(projects).where(projects.c.project_id == project_id)))
    members = (await _members(session, [project_id]))[project_id]
    progress = round(sum(m["pct"] for m in members) / len(members)) if members else 0
    stage, status = p["stage"], p["status"]
    all_done = bool(members) and all(m["state"] == "done" for m in members)
    new_stage, note = stage, None
    if stage == "planning" and any(m["state"] in ("in_progress", "done", "blocked") for m in members):
        new_stage, note = "in_progress", "Work started - project moved to In Progress"
    if new_stage in ("planning", "in_progress") and all_done:
        new_stage, note = "review", "All team members are done - project moved to Review for admin sign-off"
    elif new_stage == "review" and not all_done:
        new_stage, note = "in_progress", "Work reopened - project moved back to In Progress"
    if stage == "done":          # a signed-off project is only changed by an admin
        new_stage, note, progress = "done", None, 100
    await session.execute(update(projects).where(projects.c.project_id == project_id).values(
        progress_pct=progress, stage=new_stage, status=status, updated_at=iso()))
    if note and new_stage != stage:
        await session.execute(project_updates.insert().values(
            project_id=project_id, employee_id=None, update_type="stage_change", stage=new_stage,
            message=note, progress_pct=progress, created_at=iso()))
    return {"progress": progress, "stage": new_stage}


# ------------------------------------------------------------------ flow rendering --------------
_MARK = {"done": "[x]", "in_progress": "[>]", "blocked": "[!]", "not_started": "[ ]"}
_STATE_LABEL = {"done": "DONE", "in_progress": "IN PROGRESS", "blocked": "BLOCKED", "not_started": "NOT STARTED"}


def render_flow(p: dict, members: list[dict]) -> str:
    """A plain-text flow chart (box drawing characters) - shown in a code block, so it works in chat and voice."""
    labels = [f"{i + 1}. {STAGE_LABEL[s]}" for i, s in enumerate(STAGES)]
    w = max(len(label) for label in labels) + 4
    cur = STAGES.index(p["stage"]) if p["stage"] in STAGES else 0
    filled = round(20 * (p["progress_pct"] or 0) / 100)
    lines = [
        f"PROJECT {p['project_id']} - {p['name']}",
        f"Priority: {str(p['priority']).upper()}   Due: {p['due_date'] or 'not set'}   Status: {p['status']}",
        f"Progress: [{'#' * filled}{'-' * (20 - filled)}] {p['progress_pct']}%",
        "",
    ]
    last = len(STAGES) - 1
    for i, label in enumerate(labels):
        if i < cur or (i == cur and p["stage"] == "done"):
            tag = "[x] completed"
        elif i == cur:
            tag = "[>] CURRENT - work is here"
        else:
            tag = "[ ] pending"
        lines.append("┌" + "─" * w + "┐")
        lines.append("│ " + label.ljust(w - 2) + " │  " + tag)
        if i < last:
            lines.append("└" + "─" * (w // 2) + "┬" + "─" * (w - w // 2 - 1) + "┘")
            lines.append(" " * (w // 2 + 1) + "▼")
        else:
            lines.append("└" + "─" * w + "┘")
    lines += ["", "TEAM - who is doing what"]
    if not members:
        lines.append("  (nobody is assigned yet)")
    for m in members:
        msg = (m["last_message"] or "no update yet")[:48]
        lines.append(f"  {_MARK[m['state']]} {m['employee_id']:<5} {(m['name'] or '?'):<15.15} {(m['role_in_project'] or 'Member'):<17.17}"
                     f" {m['pct']:>3}%  {_STATE_LABEL[m['state']]:<11}  {msg}")
    blocked = [m for m in members if m["state"] == "blocked"]
    if blocked:
        lines += ["", "ATTENTION: " + ", ".join(f"{m['name']} is blocked" for m in blocked)]
    return "\n".join(lines)


# ------------------------------------------------------------------ reads ----------------------
async def list_projects(session_factory, viewer_id: str, role: str, employee_id: str | None = None,
                        status: str | None = None) -> list[dict]:
    """Admin: every project (optionally filtered to one employee). Employee: only their own projects."""
    target = employee_id if role == "admin" else viewer_id
    async with session_factory() as s:
        q = select(projects)
        if target:
            ids = select(project_assignments.c.project_id).where(
                project_assignments.c.employee_id == target, project_assignments.c.status != "removed")
            q = q.where(projects.c.project_id.in_(ids))
        if status:
            q = q.where(projects.c.status == status)
        plist = rows(await s.execute(q.order_by(projects.c.project_id)))
        team = await _members(s, [p["project_id"] for p in plist])
    for p in plist:
        m = team.get(p["project_id"], [])
        p["team_size"] = len(m)
        p["done_count"] = sum(1 for x in m if x["state"] == "done")
        p["blocked_count"] = sum(1 for x in m if x["state"] == "blocked")
        p["team"] = [{"employee_id": x["employee_id"], "name": x["name"], "state": x["state"], "pct": x["pct"],
                      "role_in_project": x["role_in_project"]} for x in m]
        mine = next((x for x in m if x["employee_id"] == viewer_id), None)
        p["my_state"] = mine["state"] if mine else None
        p["my_role"] = mine["role_in_project"] if mine else None
        p["my_pct"] = mine["pct"] if mine else None
    return plist


async def get_project(session_factory, ref: str, viewer_id: str, role: str) -> dict:
    async with session_factory() as s:
        p = await resolve_project(s, ref)
        members = (await _members(s, [p["project_id"]]))[p["project_id"]]
        if role != "admin" and not any(m["employee_id"] == viewer_id for m in members):
            raise ServiceError("You are not assigned to that project.", 403)
        timeline = rows(await s.execute(
            select(project_updates, employees.c.name.label("employee_name"))
            .select_from(project_updates.outerjoin(employees, project_updates.c.employee_id == employees.c.employee_id))
            .where(project_updates.c.project_id == p["project_id"])
            .order_by(desc(project_updates.c.update_id)).limit(40)))
        names = await employee_names(s, {p["created_by"]})
    p.update(members=members, timeline=timeline, created_by_name=names.get(p["created_by"]),
             stages=[{"key": k, "label": STAGE_LABEL[k]} for k in STAGES], flowchart=render_flow(p, members))
    return p


# ------------------------------------------------------------------ writes ---------------------
async def _assign(s, project: dict, emp: dict, role_in_project: str, assigned_by: str) -> bool:
    """Insert or re-activate an assignment. Returns False if the person is already on the project."""
    existing = one(await s.execute(select(project_assignments).where(
        project_assignments.c.project_id == project["project_id"], project_assignments.c.employee_id == emp["employee_id"])))
    now = iso()
    if existing and existing["status"] != "removed":
        return False
    if existing:
        await s.execute(update(project_assignments).where(project_assignments.c.assignment_id == existing["assignment_id"]).values(
            status="active", role_in_project=role_in_project, assigned_by=assigned_by, assigned_at=now, completed_at=None))
    else:
        await s.execute(project_assignments.insert().values(
            project_id=project["project_id"], employee_id=emp["employee_id"], role_in_project=role_in_project,
            status="active", assigned_by=assigned_by, assigned_at=now))
    await s.execute(project_updates.insert().values(
        project_id=project["project_id"], employee_id=emp["employee_id"], update_type="assigned", stage=project["stage"],
        message=f"{emp['name']} assigned as {role_in_project}", progress_pct=None, created_at=now))
    return True


async def create_project(session_factory, *, name: str, description: str = "", priority: str = "medium",
                         due_date: str | None = None, assignees: list | None = None, created_by: str,
                         actor_role: str, reason: str = "", source: str = "portal") -> dict:
    _require_admin(actor_role)
    name = (name or "").strip()
    if len(name) < 3:
        raise ServiceError("Give the project a name (at least 3 characters).", 400)
    priority = (priority or "medium").strip().lower()
    if priority not in PRIORITIES:
        raise ServiceError("Priority must be one of: low, medium, high, critical.", 400)
    due = _norm_date(due_date)
    async with session_factory() as s:
        if one(await s.execute(select(projects).where(func.lower(projects.c.name) == name.lower()))):
            raise ServiceError(f"A project named '{name}' already exists.", 409)
        people = []
        for ref in assignees or []:
            e = await resolve_employee(s, ref if isinstance(ref, str) else ref.get("employee_id", ""))
            if e["employee_id"] not in {x["employee_id"] for x in people}:
                people.append(e)
        pid, now = await _next_project_id(s), iso()
        await s.execute(projects.insert().values(
            project_id=pid, name=name, description=(description or "").strip(), status="active", stage="planning",
            priority=priority, progress_pct=0, start_date=now[:10], due_date=due, created_by=created_by,
            created_at=now, updated_at=now))
        await s.execute(project_updates.insert().values(
            project_id=pid, employee_id=created_by, update_type="created", stage="planning",
            message=f"Project created: {name}", progress_pct=0, created_at=now))
        project = {"project_id": pid, "stage": "planning"}
        await add_audit(s, action="create_project", employee_id=created_by, requested_by=created_by, status="success",
                        detail=f"{pid} {name}", reason=reason or "Project created from the portal", actor_role=actor_role, source=source)
        for e in people:
            await _assign(s, project, e, "Member", created_by)
            await add_audit(s, action="assign_project", employee_id=e["employee_id"], requested_by=created_by, status="success",
                            detail=f"{pid} {name} as Member", reason=reason or "Assigned when the project was created",
                            actor_role=actor_role, source=source)
        await s.commit()
    return await get_project(session_factory, pid, created_by, "admin")


async def assign_project(session_factory, *, project: str, employee: str, role_in_project: str = "Member",
                         assigned_by: str, actor_role: str, reason: str = "", source: str = "portal") -> dict:
    _require_admin(actor_role)
    role_in_project = (role_in_project or "Member").strip()[:40] or "Member"
    async with session_factory() as s:
        p, e = await resolve_project(s, project), await resolve_employee(s, employee)
        if p["status"] == "completed":
            raise ServiceError(f"{p['name']} is completed; reopen it before assigning people.", 400)
        if e.get("account_locked"):
            raise ServiceError(f"{e['name']}'s access is revoked, so they cannot be assigned.", 400)
        if not await _assign(s, p, e, role_in_project, assigned_by):
            raise ServiceError(f"{e['name']} is already on {p['name']}.", 409)
        await _recompute(s, p["project_id"], assigned_by)
        await add_audit(s, action="assign_project", employee_id=e["employee_id"], requested_by=assigned_by, status="success",
                        detail=f"{p['project_id']} {p['name']} as {role_in_project}",
                        reason=reason or "Assigned from the portal", actor_role=actor_role, source=source)
        await s.commit()
    return await get_project(session_factory, p["project_id"], assigned_by, "admin")


async def unassign_project(session_factory, *, project: str, employee: str, removed_by: str, actor_role: str,
                           reason: str = "", source: str = "portal") -> dict:
    _require_admin(actor_role)
    async with session_factory() as s:
        p, e = await resolve_project(s, project), await resolve_employee(s, employee)
        res = await s.execute(update(project_assignments).where(
            project_assignments.c.project_id == p["project_id"], project_assignments.c.employee_id == e["employee_id"],
            project_assignments.c.status != "removed").values(status="removed"))
        if not res.rowcount:
            raise ServiceError(f"{e['name']} is not on {p['name']}.", 404)
        await s.execute(project_updates.insert().values(
            project_id=p["project_id"], employee_id=e["employee_id"], update_type="note", stage=p["stage"],
            message=f"{e['name']} removed from the project", created_at=iso()))
        await _recompute(s, p["project_id"], removed_by)
        await add_audit(s, action="unassign_project", employee_id=e["employee_id"], requested_by=removed_by, status="success",
                        detail=f"{p['project_id']} {p['name']}", reason=reason or "Removed from the portal",
                        actor_role=actor_role, source=source)
        await s.commit()
    return await get_project(session_factory, p["project_id"], removed_by, "admin")


async def post_update(session_factory, *, project: str, employee_id: str, update_type: str, message: str = "",
                      progress_pct: int | None = None, actor_role: str = "employee", reason: str = "",
                      source: str = "portal") -> dict:
    """An assigned employee reports progress / a blocker / completion. Updates the flow chart for the admin."""
    kind = _norm_type(update_type)
    message = (message or "").strip()[:400]
    if kind in ("blocked", "note") and not message:
        raise ServiceError("Please add a short message so your admin knows what is happening.", 400)
    if progress_pct is not None:
        try:
            progress_pct = max(0, min(100, int(progress_pct)))
        except (TypeError, ValueError):
            raise ServiceError("Progress must be a number between 0 and 100.", 400)
    async with session_factory() as s:
        p = await resolve_project(s, project)
        a = one(await s.execute(select(project_assignments).where(
            project_assignments.c.project_id == p["project_id"], project_assignments.c.employee_id == employee_id,
            project_assignments.c.status != "removed")))
        if not a and not (actor_role == "admin" and kind == "note"):
            raise ServiceError("You are not assigned to that project, so you cannot post updates to it.", 403)
        if p["status"] == "completed" or p["stage"] == "done":
            raise ServiceError(f"{p['name']} is already completed.", 400)
        now = iso()
        if kind == "done":
            progress_pct = 100
            message = message or "Finished my part of the work"
        if a:
            if kind == "done":
                await s.execute(update(project_assignments).where(project_assignments.c.assignment_id == a["assignment_id"]).values(
                    status="done", completed_at=now))
            elif a["status"] == "done":   # picking the work back up
                await s.execute(update(project_assignments).where(project_assignments.c.assignment_id == a["assignment_id"]).values(
                    status="active", completed_at=None))
            if kind in ("progress", "blocked") and progress_pct is None:   # keep the % the person had already reached
                prev = await _members(s, [p["project_id"]])
                cur = next((m["pct"] for m in prev[p["project_id"]] if m["employee_id"] == employee_id and m["state"] != "done"), 0)
                progress_pct = cur if (cur or kind == "blocked") else 10
        await s.execute(project_updates.insert().values(
            project_id=p["project_id"], employee_id=employee_id, update_type=kind, stage=p["stage"],
            message=message, progress_pct=progress_pct, created_at=now))
        result = await _recompute(s, p["project_id"], employee_id)
        await add_audit(s, action="update_project", employee_id=employee_id, requested_by=employee_id, status="success",
                        detail=f"{p['project_id']} {kind}: {message}"[:300],
                        reason=reason or f"Employee reported '{kind}' from the portal", actor_role=actor_role, source=source)
        await s.commit()
    detail = await get_project(session_factory, p["project_id"], employee_id, actor_role)
    detail["moved_to"] = result["stage"]
    return detail


async def set_stage(session_factory, *, project: str, stage: str, performed_by: str, actor_role: str,
                    reason: str = "", source: str = "portal") -> dict:
    _require_admin(actor_role)
    stage = (stage or "").strip().lower().replace(" ", "_").replace("-", "_")
    if stage not in STAGES:
        raise ServiceError("Stage must be one of: planning, in_progress, review, done.", 400)
    async with session_factory() as s:
        p = await resolve_project(s, project)
        if p["stage"] == stage:
            raise ServiceError(f"{p['name']} is already in {STAGE_LABEL[stage]}.", 409)
        status = "completed" if stage == "done" else "active"
        values = dict(stage=stage, status=status, updated_at=iso())
        if stage == "done":
            values["progress_pct"] = 100
        await s.execute(update(projects).where(projects.c.project_id == p["project_id"]).values(**values))
        await s.execute(project_updates.insert().values(
            project_id=p["project_id"], employee_id=performed_by, update_type="stage_change", stage=stage,
            message=f"Admin moved the project from {STAGE_LABEL[p['stage']]} to {STAGE_LABEL[stage]}",
            progress_pct=values.get("progress_pct", p["progress_pct"]), created_at=iso()))
        await add_audit(s, action="set_project_stage", employee_id=performed_by, requested_by=performed_by, status="success",
                        detail=f"{p['project_id']} {p['stage']} -> {stage}", reason=reason or "Stage changed from the portal",
                        actor_role=actor_role, source=source)
        await s.commit()
    return await get_project(session_factory, p["project_id"], performed_by, "admin")
