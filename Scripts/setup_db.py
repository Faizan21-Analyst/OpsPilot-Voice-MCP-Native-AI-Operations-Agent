"""
Run once to (re)create all tables from the SQLAlchemy metadata.
Usage: python -m scripts.setup_db
"""
import asyncio
from pathlib import Path
from sqlalchemy import insert

from src.core.config import load_settings
from src.mcp_servers.sql.database import build_engine
# Make sure to import the table definitions we need to insert data into
from src.mcp_servers.sql.models import metadata, employees, tickets
# import ops.models too so audit_log is registered on the same metadata
import src.mcp_servers.ops.models  # noqa: F401


async def main():
    settings = load_settings()
    engine = build_engine(settings.database)

    # --- FIX: Ensure the directory for the SQLite file exists ---
    db_path = engine.url.database
    if db_path and db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        print(f"Ensured directory exists for database: {db_path}")
    # ------------------------------------------------------------

    # 1. Create all the tables (employees, tickets, audit_log)
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)
        print("Tables created:", list(metadata.tables.keys()))

    # 2. Insert the mock records
    async with engine.begin() as conn:
        await conn.execute(
            insert(employees),
            [
                {
                    "employee_id": "E001",
                    "name": "Alice Johnson",
                    "department": "Engineering",
                    "email": "alice@example.com",
                },
                {
                    "employee_id": "E002",
                    "name": "Bob Smith",
                    "department": "HR",
                    "email": "bob@example.com",
                },
                {
                    "employee_id": "E003",
                    "name": "Charlie Brown",
                    "department": "Engineering",
                    "email": "charlie@example.com",
                },
                {
                    "employee_id": "E004",
                    "name": "Diana Wilson",
                    "department": "Finance",
                    "email": "diana@example.com",
                },
            ],
        )

        await conn.execute(
            insert(tickets),
            [
                {
                    "ticket_id": "T001",
                    "employee_id": "E001",
                    "title": "Payment API failure",
                    "description": "Payment API returning HTTP 500 errors.",
                    "status": "OPEN",
                },
                {
                    "ticket_id": "T002",
                    "employee_id": "E001",
                    "title": "VPN access issue",
                    "description": "Unable to connect to corporate VPN.",
                    "status": "CLOSED",
                },
                {
                    "ticket_id": "T003",
                    "employee_id": "E002",
                    "title": "Payroll access",
                    "description": "Employee unable to access payroll portal.",
                    "status": "OPEN",
                },
                {
                    "ticket_id": "T004",
                    "employee_id": "E003",
                    "title": "Deployment failure",
                    "description": "Production deployment failed during rollout.",
                    "status": "OPEN",
                },
            ],
        )
        print("Mock data inserted successfully for employees and tickets.")

    # 3. Clean up the connection pool
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())