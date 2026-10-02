"""End-to-end test for the portal: login + advanced locking, projects, password reset, logs, analytics.
Run:  python -m pytest tests/test_portal_e2e.py -s"""
import asyncio, os, tempfile, sys, traceback
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from src.core.config import AuthSettings
from src.auth.security import create_access_token
from src.mcp_servers.sql.migrate import migrate_database
from src.portal import accounts
from src.portal.common import ServiceError
from src.api.routes import portal
from src.mcp_servers.ops import tools as ops_tools, project_tools

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (f"  -> {extra}" if (extra and not cond) else ""))


class LoginBody(BaseModel):
    username: str
    password: str


async def main():
    tmp = tempfile.mkdtemp()
    url = f"sqlite+aiosqlite:///{tmp}/t.db"
    info = migrate_database(url)
    # second run must be a no-op (idempotent)
    info2 = migrate_database(url)
    check("migration runs and is idempotent", info2["columns_added"] == [], info2)

    engine = create_async_engine(url)
    sf = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    auth_settings = AuthSettings(jwt_secret="test-secret")

    app = FastAPI()
    app.state.container = SimpleNamespace(settings=SimpleNamespace(auth=auth_settings))
    app.state.session_factory = sf

    @app.post("/login")
    async def login(body: LoginBody):
        try:
            who = await accounts.authenticate(sf, body.username, body.password, "127.0.0.1")
        except ServiceError as e:
            raise HTTPException(status_code=e.status, detail=e.message)
        return {"access_token": create_access_token(who["user_id"], who["role"], auth_settings),
                "user_id": who["user_id"], "role": who["role"]}

    app.include_router(portal.router)
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")

    async def login_as(u, p):
        r = await c.post("/login", json={"username": u, "password": p})
        return r, ({"Authorization": "Bearer " + r.json()["access_token"]} if r.status_code == 200 else None)

    r, A = await login_as("admin", "admin123"); check("admin login", r.status_code == 200, r.text)
    r, E = await login_as("employee", "employee123"); check("employee login (E001)", r.status_code == 200, r.text)
    r, C = await login_as("charlie", "employee123"); check("charlie login (E003)", r.status_code == 200, r.text)

    # ---- role separation
    r = await c.get("/admin/overview", headers=E); check("employee blocked from admin API", r.status_code == 403, r.text)
    r = await c.get("/admin/overview", headers=A); check("admin overview", r.status_code == 200 and r.json()["employees"] >= 14, r.text[:200])

    # ---- 2. more tables / data points
    r = await c.get("/admin/analytics", headers=A); j = r.json()
    check("analytics endpoint", r.status_code == 200, r.text[:300])
    check("analytics has tickets/projects/workload/security/ops_actions",
          all(k in j for k in ("tickets", "projects", "workload", "security", "ops_actions")), list(j))
    r = await c.get("/admin/employees", headers=A); emps = r.json()
    check("employees list with 14+ staff and tickets/projects counts", r.status_code == 200 and len(emps) >= 14 and "open_tickets" in emps[0], r.text[:200])
    r = await c.get("/admin/tickets", headers=A); check("admin tickets list (40+ rows)", r.status_code == 200 and len(r.json()) >= 30, str(len(r.json())))
    r = await c.get("/admin/tickets?status=open&priority=Critical", headers=A); check("ticket filters", r.status_code == 200, r.text[:200])

    # ---- 6. employee 360 + logs
    r = await c.get("/admin/employees/E003", headers=A); d = r.json()
    check("employee 360 (projects, tickets, ops actions, logins)", r.status_code == 200 and all(k in d for k in ("projects", "tickets_raised", "ops_actions_on_employee", "logins", "lock_events")), r.text[:300])
    check("E003 shows assigned project P001", any(p["project_id"] == "P001" for p in d["projects"]), str(d.get("projects"))[:200])
    r = await c.get("/admin/logs?days=30", headers=A); check("audit logs list", r.status_code == 200 and len(r.json()) > 5, r.text[:200])
    r = await c.get("/admin/logs?employee_id=E003", headers=A); check("audit logs filter by employee", r.status_code == 200, r.text[:200])
    r = await c.get("/admin/logs/actions", headers=A); check("audit action list", r.status_code == 200 and "reset_password" in r.json(), r.text[:200])

    # ---- employee sees own projects & tickets
    r = await c.get("/me/projects", headers=E); mine = r.json()
    check("employee sees only own projects", r.status_code == 200 and mine and all(p["my_state"] is not None for p in mine), r.text[:200])
    r = await c.get("/me/tickets", headers=E); check("employee sees own tickets", r.status_code == 200 and all(t["employee_id"] == "E001" for t in r.json()), r.text[:200])
    r = await c.get("/projects/P003", headers=E); check("employee blocked from project they are not on", r.status_code == 403, r.text[:200])
    r = await c.get("/me/overview", headers=E); check("employee overview", r.status_code == 200 and "stats" in r.json(), r.text[:200])

    # ---- 4. admin creates project + assigns to employees
    r = await c.post("/admin/projects", headers=A, json={"name": "Warehouse Scanner Rollout", "description": "Roll out scanners", "priority": "high", "due_date": "2026-12-01", "assignees": ["E001", "Nina"]})
    check("admin creates project with assignees", r.status_code == 200 and len(r.json()["members"]) == 2, r.text[:300])
    pid = r.json()["project_id"]
    r = await c.post("/admin/projects", headers=E, json={"name": "Sneaky"}); check("employee cannot create project", r.status_code == 403, r.text[:100])
    r = await c.post(f"/admin/projects/{pid}/assign", headers=A, json={"employee_id": "E003", "role_in_project": "DevOps"})
    check("admin assigns another employee", r.status_code == 200 and len(r.json()["members"]) == 3, r.text[:300])
    r = await c.post(f"/admin/projects/{pid}/assign", headers=A, json={"employee_id": "E003"}); check("double assignment rejected", r.status_code == 409, r.text[:100])
    r = await c.get("/admin/employees/E003", headers=A); check("assignment appears under employee in database/360", any(p["project_id"] == pid for p in r.json()["projects"]))
    r = await c.get(f"/me/projects", headers=C); check("employee sees newly assigned project", any(p["project_id"] == pid for p in r.json()))

    # ---- 5. employee updates, admin sees flow
    r = await c.post(f"/projects/{pid}/updates", headers=E, json={"update_type": "progress", "message": "Started wiring scanners", "progress_pct": 30})
    check("employee posts progress", r.status_code == 200 and r.json()["stage"] == "in_progress", r.text[:300])
    r = await c.post(f"/projects/{pid}/updates", headers=C, json={"update_type": "blocked", "message": "Need credentials"})
    check("employee posts blocked", r.status_code == 200, r.text[:300])
    r = await c.get(f"/projects/{pid}", headers=A); d = r.json()
    check("admin flow shows team states + flowchart text", "flowchart" in d and "Need credentials" in d["flowchart"] and any(m["state"] == "blocked" for m in d["members"]), d.get("flowchart", "")[:400])
    r = await c.post(f"/projects/{pid}/updates", headers=E, json={"update_type": "done"}); check("employee marks done", r.status_code == 200, r.text[:200])
    r = await c.post("/projects/P003/updates", headers=E, json={"update_type": "done"}); check("employee cannot update project not assigned", r.status_code == 403, r.text[:100])
    r = await c.post(f"/admin/projects/{pid}/stage", headers=A, json={"stage": "review"}); check("admin sets stage", r.status_code == 200, r.text[:200])

    # agent tools (voice/chat) hit same service
    t = await project_tools.update_project_progress(sf, "E014", "employee", pid, "progress", "QA started", 20)
    check("agent tool: update_project_progress", t.get("success"), str(t)[:300])
    t = await project_tools.get_project_status(sf, "E999", "admin", pid)
    check("agent tool: get_project_status returns flowchart", t.get("success") and "flowchart" in t, str(t)[:300])
    t = await project_tools.list_projects(sf, "E001", "employee")
    check("agent tool: list_projects (employee)", t.get("success") and t["count"] >= 1, str(t)[:300])
    t = await project_tools.create_project(sf, "E001", "employee", "Hack")
    check("agent tool: employee cannot create project", t.get("success") is False, str(t)[:300])

    # ---- 3. password reset -> user sets password -> DB updated
    r = await c.get("/me/password/status", headers=E); check("no reset pending initially", r.json()["pending"] is False, r.text)
    r = await c.post("/me/password/set", headers=E, json={"new_password": "Sup3rSecret!"}); check("cannot set password without approved reset", r.status_code == 403, r.text)
    res = await ops_tools.reset_password(sf, "E001", "E001", "employee", "k1", "I forgot my password")
    check("ops reset_password approves reset window", res["success"], str(res))
    r = await c.get("/me/password/status", headers=E); check("reset now pending", r.json()["pending"] is True, r.text)
    r = await c.post("/me/password/set", headers=E, json={"new_password": "short1"}); check("weak password rejected", r.status_code == 400, r.text)
    r = await c.post("/me/password/set", headers=E, json={"new_password": "Sup3rSecret!"}); check("new password saved", r.status_code == 200, r.text)
    r, _ = await login_as("employee", "employee123"); check("old password no longer works", r.status_code == 401, r.text)
    r, E = await login_as("employee", "Sup3rSecret!"); check("login with the NEW password works (database updated)", r.status_code == 200, r.text)
    r = await c.post("/me/password/set", headers=E, json={"new_password": "Another1234"}); check("reset window is single-use", r.status_code == 403, r.text)
    res = await ops_tools.reset_password(sf, "E003", "E001", "employee", "k2", "sneaky")
    check("employee cannot reset someone else's password", res["success"] is False, str(res))

    # ---- 2. advanced lock system
    for i in range(5):
        r, _ = await login_as("hannah", "wrong")
    check("5 bad logins lock the account (423)", r.status_code == 423, f"{r.status_code} {r.text}")
    r, _ = await login_as("hannah", "employee123"); check("even correct password refused while locked", r.status_code == 423, r.text)
    r = await c.get("/admin/accounts", headers=A); h = [a for a in r.json() if a["employee_id"] == "E008"][0]
    check("admin sees account as temp_locked", h["state"] == "temp_locked", str(h))
    r = await c.post("/admin/accounts/E008/unlock", headers=A, json={"reason": "verified"}); check("admin unlocks", r.status_code == 200, r.text)
    r, _ = await login_as("hannah", "employee123"); check("unlocked account can log in", r.status_code == 200, r.text)
    r = await c.post("/admin/accounts/E010/lock", headers=A, json={"reason": "Investigating suspicious activity"})
    check("admin manually locks account with reason", r.status_code == 200, r.text)
    r, _ = await login_as("julia", "employee123"); check("manually locked login refused with reason", r.status_code == 423 and "suspicious" in r.text, r.text)
    r = await c.post("/admin/accounts/E010/lock", headers=A, json={"reason": ""}); check("lock requires a reason", r.status_code == 400, r.text)
    r = await c.post("/admin/accounts/E999/lock", headers=A, json={"reason": "x"}); check("admin cannot lock self", r.status_code == 400, r.text)
    r = await c.post("/admin/accounts/E010/unlock", headers=A, json={}); check("unlock again", r.status_code == 200)
    r = await c.get("/admin/login-events?only_failed=true", headers=A); check("failed login events visible", r.status_code == 200 and len(r.json()) > 0, r.text[:200])

    # ---- ticket management + logs capture "what ops did, to whom, why"
    r = await c.patch("/admin/tickets/T005", headers=A, json={"status": "in_progress", "assigned_to": "E011", "note": "taking it"})
    check("admin updates ticket", r.status_code == 200 and r.json()["status"] == "in_progress", r.text[:300])
    r = await c.get("/admin/tickets/T005/history", headers=A); check("ticket history", r.status_code == 200 and len(r.json()) >= 2, r.text[:200])
    r = await c.get("/admin/logs?limit=20", headers=A); L = r.json()
    check("logs capture who/whom/why", all(k in L[0] for k in ("actor_name", "target_name", "reason", "action", "status")), str(L[0]))
    why = [x for x in L + (await c.get("/admin/logs?employee_id=E001", headers=A)).json() if x["action"] == "reset_password" and x["employee_id"] == "E001"]
    check("log records the reason text for the password reset", why and "forgot" in (why[0]["reason"] or ""), str(why[:1]))

    await engine.dispose()
    print(f"\nPASSED {len(PASS)}  FAILED {len(FAIL)}")
    for f in FAIL:
        print("  -", f)
    return len(FAIL)


def test_portal_end_to_end():
    """Runs the whole portal flow against a throw-away SQLite database (never touches data/opspilot.db)."""
    assert asyncio.run(main()) == 0, f"failed checks: {FAIL}"


if __name__ == "__main__":
    sys.exit(1 if asyncio.run(main()) else 0)
