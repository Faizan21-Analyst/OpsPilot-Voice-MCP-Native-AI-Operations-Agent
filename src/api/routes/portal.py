"""Portal API: employee self-service (projects, tickets, password) and the admin console."""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from src.auth.dependencies import get_current_principal, require_admin
from src.auth.models import TokenPayload
from src.portal import accounts, projects as proj, reports
from src.portal.common import ServiceError

router = APIRouter()


def _sf(request: Request):
    sf = getattr(request.app.state, "session_factory", None)
    if sf is None:
        raise HTTPException(status_code=503, detail="Database is not ready yet")
    return sf


async def _call(coro):
    try:
        return await coro
    except ServiceError as e:
        raise HTTPException(status_code=e.status, detail=e.message)


# ---------------------------------------------------------------- request bodies -----------------
class ProjectCreate(BaseModel):
    name: str
    description: str = ""
    priority: str = "medium"
    due_date: str | None = None
    assignees: list[str] = Field(default_factory=list)


class AssignBody(BaseModel):
    employee_id: str
    role_in_project: str = "Member"


class EmployeeBody(BaseModel):
    employee_id: str


class StageBody(BaseModel):
    stage: str


class UpdateBody(BaseModel):
    update_type: str
    message: str = ""
    progress_pct: int | None = None


class TicketPatch(BaseModel):
    status: str | None = None
    assigned_to: str | None = None
    priority: str | None = None
    note: str = ""


class PasswordBody(BaseModel):
    new_password: str


class LockBody(BaseModel):
    reason: str
    minutes: int | None = None


class UnlockBody(BaseModel):
    reason: str = ""


# ---------------------------------------------------------------- employee self-service ---------
@router.get("/me/overview")
async def me_overview(request: Request, p: TokenPayload = Depends(get_current_principal)):
    return await _call(reports.my_overview(_sf(request), p.user_id, p.role))


@router.get("/me/projects")
async def me_projects(request: Request, p: TokenPayload = Depends(get_current_principal)):
    return await _call(proj.list_projects(_sf(request), p.user_id, "employee"))


@router.get("/me/tickets")
async def me_tickets(request: Request, status: str | None = None, p: TokenPayload = Depends(get_current_principal)):
    return await _call(reports.list_tickets(_sf(request), viewer_id=p.user_id, role="employee", status=status))


@router.get("/projects/{project_id}")
async def project_detail(project_id: str, request: Request, p: TokenPayload = Depends(get_current_principal)):
    """Admin: any project. Employee: only a project they are assigned to."""
    return await _call(proj.get_project(_sf(request), project_id, p.user_id, p.role))


@router.post("/projects/{project_id}/updates")
async def project_post_update(project_id: str, body: UpdateBody, request: Request,
                              p: TokenPayload = Depends(get_current_principal)):
    return await _call(proj.post_update(
        _sf(request), project=project_id, employee_id=p.user_id, update_type=body.update_type, message=body.message,
        progress_pct=body.progress_pct, actor_role=p.role, reason="Employee posted an update from the portal", source="portal"))


@router.get("/me/password/status")
async def password_status(request: Request, p: TokenPayload = Depends(get_current_principal)):
    pending = await _call(accounts.get_pending_reset(_sf(request), p.user_id))
    return {"pending": bool(pending), "expires_at": pending["expires_at"] if pending else None}


@router.post("/me/password/set")
async def password_set(body: PasswordBody, request: Request, p: TokenPayload = Depends(get_current_principal)):
    """Step 2 of a reset: only works after an approved reset (agent or admin) is waiting for this employee."""
    return await _call(accounts.complete_password_reset(_sf(request), p.user_id, body.new_password))


# ---------------------------------------------------------------- admin console -----------------
@router.get("/admin/overview")
async def admin_overview(request: Request, _: TokenPayload = Depends(require_admin)):
    return await _call(reports.admin_overview(_sf(request)))


@router.get("/admin/analytics")
async def admin_analytics(request: Request, _: TokenPayload = Depends(require_admin)):
    return await _call(reports.analytics(_sf(request)))


@router.get("/admin/employees")
async def admin_employees(request: Request, _: TokenPayload = Depends(require_admin)):
    return await _call(reports.list_employees(_sf(request)))


@router.get("/admin/employees/{employee_id}")
async def admin_employee(employee_id: str, request: Request, _: TokenPayload = Depends(require_admin)):
    return await _call(reports.employee_profile(_sf(request), employee_id))


@router.get("/admin/projects")
async def admin_projects(request: Request, employee_id: str | None = None, status: str | None = None,
                         p: TokenPayload = Depends(require_admin)):
    return await _call(proj.list_projects(_sf(request), p.user_id, "admin", employee_id=employee_id, status=status))


@router.post("/admin/projects")
async def admin_create_project(body: ProjectCreate, request: Request, p: TokenPayload = Depends(require_admin)):
    return await _call(proj.create_project(
        _sf(request), name=body.name, description=body.description, priority=body.priority, due_date=body.due_date,
        assignees=body.assignees, created_by=p.user_id, actor_role=p.role, reason="Project created from the admin portal", source="portal"))


@router.post("/admin/projects/{project_id}/assign")
async def admin_assign(project_id: str, body: AssignBody, request: Request, p: TokenPayload = Depends(require_admin)):
    return await _call(proj.assign_project(
        _sf(request), project=project_id, employee=body.employee_id, role_in_project=body.role_in_project,
        assigned_by=p.user_id, actor_role=p.role, reason="Assigned from the admin portal", source="portal"))


@router.post("/admin/projects/{project_id}/unassign")
async def admin_unassign(project_id: str, body: EmployeeBody, request: Request, p: TokenPayload = Depends(require_admin)):
    return await _call(proj.unassign_project(
        _sf(request), project=project_id, employee=body.employee_id, removed_by=p.user_id, actor_role=p.role,
        reason="Removed from the admin portal", source="portal"))


@router.post("/admin/projects/{project_id}/stage")
async def admin_stage(project_id: str, body: StageBody, request: Request, p: TokenPayload = Depends(require_admin)):
    return await _call(proj.set_stage(
        _sf(request), project=project_id, stage=body.stage, performed_by=p.user_id, actor_role=p.role,
        reason="Stage changed from the admin portal", source="portal"))


@router.get("/admin/tickets")
async def admin_tickets(request: Request, status: str | None = None, priority: str | None = None,
                        category: str | None = None, employee_id: str | None = None, p: TokenPayload = Depends(require_admin)):
    return await _call(reports.list_tickets(_sf(request), viewer_id=p.user_id, role="admin", status=status,
                                            priority=priority, category=category, employee_id=employee_id))


@router.patch("/admin/tickets/{ticket_id}")
async def admin_ticket_update(ticket_id: str, body: TicketPatch, request: Request, p: TokenPayload = Depends(require_admin)):
    return await _call(reports.update_ticket(
        _sf(request), ticket_id=ticket_id, performed_by=p.user_id, actor_role=p.role, status=body.status,
        assigned_to=body.assigned_to, priority=body.priority, note=body.note, source="portal"))


@router.get("/admin/tickets/{ticket_id}/history")
async def admin_ticket_history(ticket_id: str, request: Request, _: TokenPayload = Depends(require_admin)):
    return await _call(reports.ticket_history(_sf(request), ticket_id))


@router.get("/admin/logs")
async def admin_logs(request: Request, employee_id: str | None = None, action: str | None = None,
                     status: str | None = None, source: str | None = None, days: int | None = None,
                     limit: int = 200, offset: int = 0, _: TokenPayload = Depends(require_admin)):
    return await _call(reports.list_audit(_sf(request), employee_id=employee_id, action=action, status=status,
                                          source=source, days=days, limit=limit, offset=offset))


@router.get("/admin/logs/actions")
async def admin_log_actions(request: Request, _: TokenPayload = Depends(require_admin)):
    return await _call(reports.audit_actions(_sf(request)))


@router.get("/admin/login-events")
async def admin_login_events(request: Request, only_failed: bool = False, employee_id: str | None = None,
                             limit: int = 100, _: TokenPayload = Depends(require_admin)):
    return await _call(reports.list_login_events(_sf(request), only_failed=only_failed, employee_id=employee_id, limit=limit))


@router.get("/admin/accounts")
async def admin_accounts(request: Request, _: TokenPayload = Depends(require_admin)):
    return await _call(accounts.list_accounts(_sf(request)))


@router.post("/admin/accounts/{employee_id}/lock")
async def admin_lock(employee_id: str, body: LockBody, request: Request, p: TokenPayload = Depends(require_admin)):
    return await _call(accounts.lock_account(_sf(request), employee_id, body.reason, p.user_id, body.minutes))


@router.post("/admin/accounts/{employee_id}/unlock")
async def admin_unlock(employee_id: str, body: UnlockBody, request: Request, p: TokenPayload = Depends(require_admin)):
    return await _call(accounts.unlock_account(_sf(request), employee_id, body.reason, p.user_id))
