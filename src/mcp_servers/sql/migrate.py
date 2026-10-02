"""
Idempotent schema migration + demo data. Safe to run on EVERY startup:

  * creates any missing tables (never drops or rewrites anything),
  * adds any missing nullable/defaulted columns to existing tables,
  * seeds demo data only where a section is still empty.

Set OPSPILOT_SEED_DEMO=0 to skip the demo data (do this in production).
"""
import os
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine, inspect, select, update, func
from sqlalchemy.exc import OperationalError

from src.mcp_servers.sql.models import (
    metadata, employees, tickets, departments, ticket_events, users,
    login_events, account_lock_events, projects, project_assignments, project_updates,
)
from src.mcp_servers.ops.models import audit_log  # noqa: F401  (registers audit_log on the shared metadata)

_FMT = "%Y-%m-%d %H:%M:%S"
DEMO_PASSWORD = "employee123"


def sync_url(url: str) -> str:
    for driver in ("+aiosqlite", "+asyncpg", "+aiomysql"):
        url = url.replace(driver, "")
    return url


# ----------------------------------------------------------------------------- schema -----------
def _add_missing_columns(engine) -> list[str]:
    added = []
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in have:
                    continue
                ddl = f'ALTER TABLE {table.name} ADD COLUMN {col.name} {col.type.compile(dialect=engine.dialect)}'
                if col.server_default is not None:
                    arg = str(col.server_default.arg)
                    ddl += f" DEFAULT {arg}" if arg.lstrip("-").isdigit() else f" DEFAULT '{arg}'"
                elif not col.nullable:
                    continue  # cannot add a NOT NULL column without a default to a populated table
                conn.exec_driver_sql(ddl)
                added.append(f"{table.name}.{col.name}")
    return added


def migrate_schema(engine) -> list[str]:
    metadata.create_all(engine)          # new tables only (checkfirst)
    return _add_missing_columns(engine)  # new columns on old tables


# ----------------------------------------------------------------------------- demo data --------
def _ts(now: datetime, days: float = 0, hours: float = 0) -> str:
    return (now - timedelta(days=days, hours=hours)).strftime(_FMT)


_DEPARTMENTS = [
    ("ENG", "Engineering", "E005", "Bengaluru"),
    ("HR", "HR", "E002", "Mumbai"),
    ("FIN", "Finance", "E004", "Mumbai"),
    ("OPS", "IT Operations", "E006", "Pune"),
    ("SAL", "Sales", "E013", "Delhi"),
    ("SEC", "Security", "E008", "Pune"),
]

# (id, name, department, email, job_title, manager, hired_days_ago, location)
_EMPLOYEES = [
    ("E001", "Alice Johnson", "Engineering", "alice@example.com", "Senior Engineer", "E005", 1500, "Bengaluru"),
    ("E002", "Bob Smith", "HR", "bob@example.com", "HR Manager", None, 2100, "Mumbai"),
    ("E003", "Charlie Brown", "Engineering", "charlie@example.com", "DevOps Engineer", "E005", 900, "Bengaluru"),
    ("E004", "Diana Wilson", "Finance", "diana@example.com", "Finance Lead", None, 1800, "Mumbai"),
    ("E005", "Ethan Clark", "Engineering", "ethan@example.com", "Engineering Manager", None, 2400, "Bengaluru"),
    ("E006", "Fiona Davis", "IT Operations", "fiona@example.com", "Systems Administrator", "E999", 1300, "Pune"),
    ("E007", "George Miller", "Sales", "george@example.com", "Account Executive", "E013", 700, "Delhi"),
    ("E008", "Hannah Lee", "Security", "hannah@example.com", "Security Analyst", None, 1100, "Pune"),
    ("E009", "Ivan Petrov", "Engineering", "ivan@example.com", "Backend Engineer", "E005", 600, "Bengaluru"),
    ("E010", "Julia Roberts", "Finance", "julia@example.com", "Accountant", "E004", 500, "Mumbai"),
    ("E011", "Kevin Patel", "IT Operations", "kevin@example.com", "Network Engineer", "E006", 1000, "Pune"),
    ("E012", "Laura Nguyen", "HR", "laura@example.com", "Recruiter", "E002", 400, "Mumbai"),
    ("E013", "Mohan Rao", "Sales", "mohan@example.com", "Sales Manager", None, 1900, "Delhi"),
    ("E014", "Nina Alvarez", "Engineering", "nina@example.com", "QA Engineer", "E005", 300, "Bengaluru"),
    ("E999", "Admin User", "IT Operations", "admin@example.com", "IT Administrator", None, 3000, "Pune"),
]

_TICKET_CATALOG = {
    "Access": ["Cannot open shared drive", "Payroll portal access", "Need access to Jira board"],
    "Network": ["VPN keeps disconnecting", "Slow Wi-Fi on 3rd floor", "VPN access issue"],
    "Hardware": ["Laptop battery draining fast", "Monitor flickering", "Keyboard keys stuck"],
    "Software": ["Excel crashes on launch", "Design tool install request", "IDE license expired"],
    "Account": ["Forgot password", "Account locked out", "MFA device lost"],
    "Application": ["Payment API returning errors", "CI pipeline failed", "Report export failing"],
    "Email": ["Mailbox is full", "Calendar invites missing"],
}


def _seed_org(conn, now):
    have_dept = {r[0] for r in conn.execute(select(departments.c.dept_id))}
    for d in _DEPARTMENTS:
        if d[0] not in have_dept:
            conn.execute(departments.insert().values(dept_id=d[0], name=d[1], head_id=d[2], location=d[3]))

    have_emp = {r[0] for r in conn.execute(select(employees.c.employee_id))}
    for eid, name, dept, email, title, mgr, hired, loc in _EMPLOYEES:
        extra = dict(job_title=title, manager_id=mgr, hire_date=_ts(now, hired)[:10],
                     location=loc, employment_status="active")
        if eid in have_emp:  # keep the original row; only fill the new analysis columns that are still empty
            row = conn.execute(select(employees).where(employees.c.employee_id == eid)).mappings().first()
            fill = {k: v for k, v in extra.items() if row.get(k) is None}
            if fill:
                conn.execute(update(employees).where(employees.c.employee_id == eid).values(**fill))
        else:
            conn.execute(employees.insert().values(
                employee_id=eid, name=name, department=dept, email=email, account_locked=0, **extra))


def _seed_users(conn, now):
    if conn.execute(select(func.count()).select_from(users)).scalar():
        return
    from src.auth.security import hash_password  # local import: bcrypt only needed when seeding
    demo_hash, admin_hash = hash_password(DEMO_PASSWORD), hash_password("admin123")
    for eid, name, *_ in _EMPLOYEES:
        if eid == "E999":
            username, role, pw = "admin", "admin", admin_hash
        elif eid == "E001":
            username, role, pw = "employee", "employee", demo_hash   # same login the original demo used
        else:
            username, role, pw = name.split()[0].lower(), "employee", demo_hash
        conn.execute(users.insert().values(
            username=username, employee_id=eid, password_hash=pw, role=role,
            created_at=_ts(now, 90), password_changed_at=_ts(now, 90)))


def _seed_tickets(conn, now):
    rnd = random.Random(42)
    staff = ["E006", "E011", "E999"]
    staff_names = [e[0] for e in _EMPLOYEES if e[0] != "E999"]

    # Back-fill the original four tickets so they take part in analytics too.
    fill = {"T001": ("High", "Application", "E003", 9, "P001"), "T002": ("Medium", "Network", "E011", 25, None),
            "T003": ("Medium", "Access", "E006", 6, None), "T004": ("Critical", "Application", "E003", 3, "P001")}
    for tid, (prio, cat, who, age, pid) in fill.items():
        row = conn.execute(select(tickets).where(tickets.c.ticket_id == tid)).mappings().first()
        if row and row["created_at"] is None:
            closed = str(row["status"]).lower() in ("closed", "resolved")
            conn.execute(update(tickets).where(tickets.c.ticket_id == tid).values(
                priority=prio, category=cat, assigned_to=who, created_at=_ts(now, age),
                updated_at=_ts(now, age - 1), resolved_at=_ts(now, age - 1) if closed else None, project_id=pid))
            conn.execute(ticket_events.insert().values(
                ticket_id=tid, event_type="created", to_value="open", performed_by=row["employee_id"],
                note="Ticket created", created_at=_ts(now, age)))

    if conn.execute(select(func.count()).select_from(tickets)).scalar() >= 12:
        return
    for i in range(5, 45):
        cat = rnd.choice(list(_TICKET_CATALOG))
        title = rnd.choice(_TICKET_CATALOG[cat])
        status = rnd.choices(["open", "in_progress", "resolved", "closed"], [30, 25, 25, 20])[0]
        prio = rnd.choices(["Low", "Medium", "High", "Critical"], [25, 45, 22, 8])[0]
        age = rnd.uniform(0.2, 60)
        created = now - timedelta(days=age)
        solved = status in ("resolved", "closed")
        resolved = created + timedelta(hours=rnd.uniform(2, 72)) if solved else None
        if resolved and resolved > now:
            resolved = now
        tid = f"T{i:03d}"
        owner = rnd.choice(staff_names)
        assignee = None if status == "open" and rnd.random() < .5 else rnd.choice(staff)
        conn.execute(tickets.insert().values(
            ticket_id=tid, employee_id=owner, title=title, description=f"{title}. Reported by {owner}.",
            status=status, priority=prio, category=cat, assigned_to=assignee,
            created_at=created.strftime(_FMT), updated_at=(resolved or created).strftime(_FMT),
            resolved_at=resolved.strftime(_FMT) if resolved else None,
            project_id=rnd.choice(["P001", "P003", None]) if cat == "Application" else None))
        conn.execute(ticket_events.insert().values(
            ticket_id=tid, event_type="created", to_value="open", performed_by=owner,
            note="Ticket created", created_at=created.strftime(_FMT)))
        if status != "open":
            conn.execute(ticket_events.insert().values(
                ticket_id=tid, event_type="status_change", from_value="open", to_value=status,
                performed_by=assignee or "E999", note="Status updated",
                created_at=(resolved or created + timedelta(hours=3)).strftime(_FMT)))


_PROJECTS = [
    # id, name, description, stage, status, priority, progress, start_ago, due_in, assignees[(eid, role, status, pct)], updates
    ("P001", "Payment Gateway Upgrade", "Migrate the payment gateway to the v3 API and fix the HTTP 500 errors.",
     "in_progress", "active", "high", 50, 40, 20,
     [("E001", "Lead Developer", "done", 100), ("E003", "DevOps", "active", 60),
      ("E009", "Backend Developer", "active", 40), ("E014", "QA", "active", 0)],
     [("E001", "progress", "API integration 70% complete", 70, 25), ("E001", "done", "API integration finished and merged", 100, 12),
      ("E003", "progress", "Staging environment provisioned", 60, 6), ("E009", "progress", "Refactoring retry logic", 40, 3),
      ("E003", "blocked", "Waiting for production credentials from Security", None, 1)]),
    ("P002", "Employee Onboarding Portal", "Self-service portal for new-hire paperwork and equipment requests.",
     "planning", "active", "medium", 10, 10, 60,
     [("E002", "Product Owner", "active", 30), ("E012", "HR Analyst", "active", 0), ("E005", "Tech Lead", "active", 0)],
     [("E002", "progress", "Requirements workshop completed", 30, 4)]),
    ("P003", "Q4 Budget Automation", "Automate Q4 budget consolidation and variance reports.",
     "in_progress", "active", "medium", 70, 30, 15,
     [("E004", "Project Lead", "active", 80), ("E010", "Analyst", "active", 60)],
     [("E004", "progress", "Variance model validated", 80, 5), ("E010", "progress", "Loading cost-centre data", 60, 2)]),
    ("P004", "Zero-Trust Network Rollout", "Roll out zero-trust access controls across all offices.",
     "review", "active", "critical", 100, 70, 5,
     [("E008", "Security Lead", "done", 100), ("E011", "Network Engineer", "done", 100), ("E006", "Sysadmin", "done", 100)],
     [("E011", "done", "Pune and Delhi sites migrated", 100, 9), ("E006", "done", "Policies deployed to all servers", 100, 6),
      ("E008", "done", "Penetration test passed", 100, 2)]),
    ("P005", "Legacy VPN Decommission", "Retire the old VPN concentrator after migration.",
     "done", "completed", "low", 100, 120, -20,
     [("E011", "Network Engineer", "done", 100), ("E006", "Sysadmin", "done", 100)],
     [("E011", "done", "Concentrator powered off", 100, 25), ("E006", "done", "Decommission documented", 100, 22)]),
]


def _seed_projects(conn, now):
    if conn.execute(select(func.count()).select_from(projects)).scalar():
        return
    for pid, name, desc, stage, status, prio, pct, start_ago, due_in, team, ups in _PROJECTS:
        conn.execute(projects.insert().values(
            project_id=pid, name=name, description=desc, stage=stage, status=status, priority=prio,
            progress_pct=pct, start_date=_ts(now, start_ago)[:10], due_date=_ts(now, -due_in)[:10],
            created_by="E999", created_at=_ts(now, start_ago), updated_at=_ts(now, 1)))
        conn.execute(project_updates.insert().values(
            project_id=pid, employee_id="E999", update_type="created", stage="planning",
            message=f"Project created: {name}", progress_pct=0, created_at=_ts(now, start_ago)))
        for eid, role, st, _pct in team:
            conn.execute(project_assignments.insert().values(
                project_id=pid, employee_id=eid, role_in_project=role, status=st, assigned_by="E999",
                assigned_at=_ts(now, start_ago - 1 if start_ago > 1 else 0),
                completed_at=_ts(now, 3) if st == "done" else None))
        for eid, kind, msg, p, ago in ups:
            conn.execute(project_updates.insert().values(
                project_id=pid, employee_id=eid, update_type=kind, stage=stage, message=msg,
                progress_pct=p, created_at=_ts(now, ago)))


def _seed_security_and_audit(conn, now):
    rnd = random.Random(7)
    if not conn.execute(select(func.count()).select_from(login_events)).scalar():
        names = {e[0]: (e[1].split()[0].lower() if e[0] not in ("E001", "E999") else ("employee" if e[0] == "E001" else "admin"))
                 for e in _EMPLOYEES}
        for day in range(14, -1, -1):
            for eid, uname in names.items():
                if rnd.random() < .7:
                    conn.execute(login_events.insert().values(
                        username=uname, employee_id=eid, success=1, reason="ok", ip="10.0.0.%d" % rnd.randint(2, 200),
                        created_at=_ts(now, day, rnd.uniform(0, 9))))
                if rnd.random() < .06:
                    conn.execute(login_events.insert().values(
                        username=uname, employee_id=eid, success=0, reason="bad_password", ip="10.0.0.%d" % rnd.randint(2, 200),
                        created_at=_ts(now, day, rnd.uniform(0, 9))))
        conn.execute(account_lock_events.insert().values(
            employee_id="E007", action="locked", lock_type="auto", reason="5 consecutive failed logins (lockout #1)",
            performed_by="system", locked_until=_ts(now, 19.99), created_at=_ts(now, 20)))
        conn.execute(account_lock_events.insert().values(
            employee_id="E007", action="unlocked", lock_type="manual", reason="Identity verified by phone",
            performed_by="E999", created_at=_ts(now, 19.9)))

    if not conn.execute(select(func.count()).select_from(audit_log).where(audit_log.c.source == "seed")).scalar():
        seeds = [
            ("reset_password", "E003", "E999", "success", "approved password reset issued", "Charlie forgot his password after leave", "admin", 28),
            ("create_ticket", "E001", "E001", "success", "category=Application", "Payment API returning HTTP 500", "employee", 12),
            ("create_ticket", "E009", "E009", "success", "category=Access", "Need access to the staging cluster", "employee", 11),
            ("reset_password", "E005", "E002", "denied", "scope_violation: 'E002' cannot act on 'E005'", "HR tried to reset a manager's password", "employee", 9),
            ("create_project", "E999", "E999", "success", "P004 Zero-Trust Network Rollout", "Create the zero-trust rollout project", "admin", 70),
            ("assign_project", "E008", "E999", "success", "P004 as Security Lead", "Security owns the zero-trust rollout", "admin", 69),
            ("assign_project", "E014", "E999", "success", "P001 as QA", "Need QA coverage for the gateway upgrade", "admin", 38),
            ("account_locked", "E007", "system", "success", "locked automatically", "5 consecutive failed logins (lockout #1)", "system", 20),
            ("account_unlocked", "E007", "E999", "success", "all lock flags cleared", "Identity verified by phone", "admin", 19),
            ("revoke_access", "E012", "E999", "denied", "duplicate_request (already processed)", "Contractor offboarding retry", "admin", 15),
            ("update_project", "E001", "E001", "success", "P001 done: API integration finished", "I finished the API integration", "employee", 12),
            ("update_project", "E003", "E003", "success", "P001 blocked: waiting for production credentials", "Blocked on credentials from Security", "employee", 1),
            ("set_project_stage", "E999", "E999", "success", "P005 -> done", "Legacy VPN decommission signed off", "admin", 18),
            ("create_ticket", "E004", "E004", "success", "category=Software", "Excel crashes when opening the budget file", "employee", 4),
        ]
        for i, (action, target, by, st, detail, why, role, ago) in enumerate(seeds):
            conn.execute(audit_log.insert().values(
                action=action, employee_id=target, requested_by=by, idempotency_key=f"seed:{i}", status=st,
                detail=detail, reason=why, actor_role=role, source="seed",
                created_at=(now - timedelta(days=ago)).replace(tzinfo=None)))


def seed_demo_data(engine) -> None:
    now = datetime.now(timezone.utc)
    with engine.begin() as conn:
        _seed_org(conn, now)
        _seed_users(conn, now)
        _seed_projects(conn, now)     # before tickets: tickets reference project ids
        _seed_tickets(conn, now)
        _seed_security_and_audit(conn, now)


# ----------------------------------------------------------------------------- entry point ------
def migrate_database(db_url: str, seed: bool | None = None) -> dict:
    """Run schema migration (+ demo seed unless OPSPILOT_SEED_DEMO=0). Retries if SQLite is briefly locked."""
    url = sync_url(db_url)
    if url.startswith("sqlite") and ":memory:" not in url:
        path = url.split("///", 1)[-1]
        if path:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, connect_args={"timeout": 30} if url.startswith("sqlite") else {})
    if seed is None:
        seed = os.getenv("OPSPILOT_SEED_DEMO", "1") != "0"
    last = None
    for attempt in range(4):
        try:
            added = migrate_schema(engine)
            if seed:
                seed_demo_data(engine)
            engine.dispose()
            return {"columns_added": added, "seeded": seed}
        except OperationalError as e:   # another process is migrating at the same moment
            last = e
            time.sleep(1 + attempt)
    engine.dispose()
    raise last


if __name__ == "__main__":
    from src.core.config import load_settings
    print(migrate_database(load_settings().database.url))
