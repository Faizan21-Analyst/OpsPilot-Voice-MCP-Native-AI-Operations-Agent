import asyncio
from pathlib import Path
from sqlalchemy import insert

# Import your settings loader
from src.core.config import load_settings

from src.mcp_servers.sql.database import build_engine
from src.mcp_servers.sql.models import metadata, employees, tickets
from src.mcp_servers.ops.models import audit_log 


async def init_database():
    # 1. Load settings and CREATE the engine
    settings = load_settings()
    
    # NOTE: If your config nests the database settings, this might be `settings.database`
    # We pass the settings into the function to get the actual engine object
# To this:
    engine = build_engine(settings.database)
    # 2. Ensure the directory for the SQLite file exists
    db_path = engine.url.database
    if db_path and db_path != ":memory:":
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        print(f"Ensured directory exists for database: {db_path}")

    # 3. Create all tables (this will now include audit_log)
    async with engine.begin() as connection:
        await connection.run_sync(
            metadata.create_all
        )

    # 4. Insert dummy data (using the engine object we created)
    async with engine.begin() as connection:
        await connection.execute(
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

        await connection.execute(
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
        
        # We don't insert mock data into audit_log because an audit table 
        # should naturally start empty and only fill up as tools are used!

    print("Database initialized successfully.")
    
    # 5. Cleanly close the connection pool
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(init_database())