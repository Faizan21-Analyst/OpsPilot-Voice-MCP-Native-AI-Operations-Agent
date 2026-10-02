"""Project tools exposed to the agent (chat + voice). All real rules live in src/portal/projects.py."""
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.portal import projects as svc
from src.portal.common import ServiceError


def _fail(e: ServiceError) -> dict:
    return {"success": False, "reason": e.message}


def _summary(p: dict) -> dict:
    return {k: p.get(k) for k in ("project_id", "name", "stage", "status", "priority", "progress_pct", "due_date",
                                  "team_size", "done_count", "blocked_count", "my_state", "my_pct")}


def _status_view(p: dict) -> dict:
    """Compact enough for the model; `flowchart` must be shown to the user verbatim in a code block."""
    return {
        "success": True,
        "project_id": p["project_id"], "name": p["name"], "stage": p["stage"], "status": p["status"],
        "priority": p["priority"], "progress_pct": p["progress_pct"], "due_date": p["due_date"],
        "team": [{"employee_id": m["employee_id"], "name": m["name"], "role": m["role_in_project"], "state": m["state"],
                  "pct": m["pct"], "last_update": m["last_message"]} for m in p["members"]],
        "recent_activity": [f"{t['created_at']} {t['employee_name'] or 'system'}: {t['message']}" for t in p["timeline"][:6]],
        "flowchart": p["flowchart"],
    }


async def list_projects(sf: async_sessionmaker, requested_by: str, requester_role: str, employee_id: str | None = None,
                        status: str | None = None) -> dict:
    try:
        # Employees only ever see their own projects; admins can see everything or filter to one person.
        who = None
        if requester_role == "admin" and employee_id:
            async with sf() as s:
                who = (await svc.resolve_employee(s, employee_id))["employee_id"]
        items = await svc.list_projects(sf, requested_by, requester_role, employee_id=who, status=status)
        return {"success": True, "count": len(items), "projects": [_summary(p) for p in items]}
    except ServiceError as e:
        return _fail(e)


async def get_project_status(sf: async_sessionmaker, requested_by: str, requester_role: str, project: str) -> dict:
    try:
        return _status_view(await svc.get_project(sf, project, requested_by, requester_role))
    except ServiceError as e:
        return _fail(e)


async def create_project(sf: async_sessionmaker, requested_by: str, requester_role: str, name: str, description: str = "",
                         priority: str = "medium", due_date: str | None = None, assignee_ids: list[str] | None = None,
                         reason: str = "") -> dict:
    try:
        p = await svc.create_project(sf, name=name, description=description, priority=priority, due_date=due_date,
                                     assignees=assignee_ids or [], created_by=requested_by, actor_role=requester_role,
                                     reason=reason, source="agent")
        return _status_view(p) | {"detail": f"Project {p['project_id']} '{p['name']}' created with {len(p['members'])} team member(s)."}
    except ServiceError as e:
        return _fail(e)


async def assign_project(sf: async_sessionmaker, requested_by: str, requester_role: str, project: str, employee_id: str,
                         role_in_project: str = "Member", reason: str = "") -> dict:
    try:
        p = await svc.assign_project(sf, project=project, employee=employee_id, role_in_project=role_in_project,
                                     assigned_by=requested_by, actor_role=requester_role, reason=reason, source="agent")
        who = next((m for m in p["members"] if m["employee_id"].upper() == employee_id.upper() or (m["name"] or "").lower() == employee_id.lower()), None)
        return _status_view(p) | {"detail": f"{who['name'] if who else employee_id} is now assigned to {p['name']} as {role_in_project}."}
    except ServiceError as e:
        return _fail(e)


async def update_project_progress(sf: async_sessionmaker, requested_by: str, requester_role: str, project: str,
                                  update_type: str, message: str = "", progress_pct: int | None = None,
                                  reason: str = "") -> dict:
    try:
        p = await svc.post_update(sf, project=project, employee_id=requested_by, update_type=update_type, message=message,
                                  progress_pct=progress_pct, actor_role=requester_role, reason=reason, source="agent")
        return _status_view(p) | {"detail": f"Update recorded. {p['name']} is now in the '{p['stage']}' stage at {p['progress_pct']}%."}
    except ServiceError as e:
        return _fail(e)


async def set_project_stage(sf: async_sessionmaker, requested_by: str, requester_role: str, project: str, stage: str,
                            reason: str = "") -> dict:
    try:
        p = await svc.set_stage(sf, project=project, stage=stage, performed_by=requested_by, actor_role=requester_role,
                                reason=reason, source="agent")
        return _status_view(p) | {"detail": f"{p['name']} moved to {p['stage']}."}
    except ServiceError as e:
        return _fail(e)
