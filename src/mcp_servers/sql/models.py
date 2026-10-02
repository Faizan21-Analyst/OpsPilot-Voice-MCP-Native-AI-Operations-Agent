from sqlalchemy import (
    MetaData,
    Table,
    Column,
    String,
    Integer,
    UniqueConstraint,
)


metadata = MetaData()

# NOTE: all timestamp columns below are stored as "YYYY-MM-DD HH:MM:SS" UTC strings
# (see src/portal/common.py: iso()). New columns are always nullable/defaulted so
# src/mcp_servers/sql/migrate.py can add them to an existing database safely.


# ---------------------------------------------------------------- original tables ----------------
employees = Table(
    "employees",
    metadata,
    Column("employee_id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("department", String, nullable=False),
    Column("email", String, nullable=False),
    Column("account_locked", Integer, nullable=False, server_default="0"),
    # --- v2 analysis columns (nullable; added to existing DBs by migrate.py) ---
    Column("job_title", String),
    Column("manager_id", String),
    Column("hire_date", String),
    Column("location", String),
    Column("employment_status", String),
)


tickets = Table(
    "tickets",
    metadata,
    Column("ticket_id", String, primary_key=True),
    Column("employee_id", String, nullable=False),
    Column("title", String, nullable=False),
    Column("description", String, nullable=False),
    Column("status", String, nullable=False),
    # --- v2 analysis columns (nullable; added to existing DBs by migrate.py) ---
    Column("priority", String),
    Column("category", String),
    Column("assigned_to", String),
    Column("created_at", String),
    Column("updated_at", String),
    Column("resolved_at", String),
    Column("project_id", String),
)


# ---------------------------------------------------------------- v2: organisation --------------
departments = Table(
    "departments",
    metadata,
    Column("dept_id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("head_id", String),
    Column("location", String),
)


ticket_events = Table(
    "ticket_events",
    metadata,
    Column("event_id", Integer, primary_key=True, autoincrement=True),
    Column("ticket_id", String, nullable=False),
    Column("event_type", String, nullable=False),   # created | status_change | assigned | priority_change | comment
    Column("from_value", String),
    Column("to_value", String),
    Column("performed_by", String),
    Column("note", String),
    Column("created_at", String),
)


# ---------------------------------------------------------------- v2: accounts & security --------
users = Table(
    "users",
    metadata,
    Column("user_id", Integer, primary_key=True, autoincrement=True),
    Column("username", String, nullable=False, unique=True),
    Column("employee_id", String, nullable=False),
    Column("password_hash", String, nullable=False),
    Column("role", String, nullable=False, server_default="employee"),
    Column("failed_attempts", Integer, nullable=False, server_default="0"),
    Column("lock_count", Integer, nullable=False, server_default="0"),
    Column("locked_until", String),
    Column("manually_locked", Integer, nullable=False, server_default="0"),
    Column("lock_reason", String),
    Column("last_login_at", String),
    Column("password_changed_at", String),
    Column("created_at", String),
)


password_resets = Table(
    "password_resets",
    metadata,
    Column("reset_id", Integer, primary_key=True, autoincrement=True),
    Column("employee_id", String, nullable=False),
    Column("requested_by", String, nullable=False),
    Column("status", String, nullable=False),        # pending | used | expired | cancelled
    Column("created_at", String),
    Column("expires_at", String),
    Column("used_at", String),
)


login_events = Table(
    "login_events",
    metadata,
    Column("event_id", Integer, primary_key=True, autoincrement=True),
    Column("username", String),
    Column("employee_id", String),
    Column("success", Integer, nullable=False, server_default="0"),
    Column("reason", String),
    Column("ip", String),
    Column("created_at", String),
)


account_lock_events = Table(
    "account_lock_events",
    metadata,
    Column("event_id", Integer, primary_key=True, autoincrement=True),
    Column("employee_id", String, nullable=False),
    Column("action", String, nullable=False),        # locked | unlocked
    Column("lock_type", String),                     # auto | manual | revoked
    Column("reason", String),
    Column("performed_by", String),
    Column("locked_until", String),
    Column("created_at", String),
)


# ---------------------------------------------------------------- v2: projects ------------------
projects = Table(
    "projects",
    metadata,
    Column("project_id", String, primary_key=True),
    Column("name", String, nullable=False),
    Column("description", String),
    Column("status", String, nullable=False, server_default="active"),     # active | on_hold | completed
    Column("stage", String, nullable=False, server_default="planning"),    # planning | in_progress | review | done
    Column("priority", String, server_default="medium"),                   # low | medium | high | critical
    Column("progress_pct", Integer, nullable=False, server_default="0"),
    Column("start_date", String),
    Column("due_date", String),
    Column("created_by", String),
    Column("created_at", String),
    Column("updated_at", String),
)


project_assignments = Table(
    "project_assignments",
    metadata,
    Column("assignment_id", Integer, primary_key=True, autoincrement=True),
    Column("project_id", String, nullable=False),
    Column("employee_id", String, nullable=False),
    Column("role_in_project", String),
    Column("status", String, nullable=False, server_default="active"),     # active | done | removed
    Column("assigned_by", String),
    Column("assigned_at", String),
    Column("completed_at", String),
    UniqueConstraint("project_id", "employee_id", name="uq_project_employee"),
)


project_updates = Table(
    "project_updates",
    metadata,
    Column("update_id", Integer, primary_key=True, autoincrement=True),
    Column("project_id", String, nullable=False),
    Column("employee_id", String),
    Column("update_type", String, nullable=False),   # created | assigned | progress | blocked | done | note | stage_change
    Column("stage", String),
    Column("message", String),
    Column("progress_pct", Integer),
    Column("created_at", String),
)
