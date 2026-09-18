from sqlalchemy.ext.asyncio import async_sessionmaker

from src.mcp_servers.sql.models import employees, tickets
from sqlalchemy import select


async def get_employee(
    session_factory: async_sessionmaker,
    employee_id: str,
) -> dict | None:
    async with session_factory() as session:
        query = select(employees).where(employees.c.employee_id == employee_id)
        result = await session.execute(query)
        row = result.mappings().first()
        return dict(row) if row else None


async def search_employees(
    session_factory: async_sessionmaker,
    name: str | None = None,
    department: str | None = None,
    limit: int = 20,
) -> list[dict]:
    async with session_factory() as session:
        query = select(employees)

        if name:
            query = query.where(employees.c.name.ilike(f"%{name}%"))
        if department:
            query = query.where(employees.c.department == department)

        query = query.limit(limit)

        result = await session.execute(query)
        return [dict(row) for row in result.mappings().all()]


async def get_tickets_for_employee(
    session_factory: async_sessionmaker,
    employee_id: str,
    status: str | None = None,
    limit: int = 20,
) -> list[dict]:
    async with session_factory() as session:
        query = select(tickets).where(tickets.c.employee_id == employee_id)

        if status:
            query = query.where(tickets.c.status == status)

        query = query.limit(limit)

        result = await session.execute(query)
        return [dict(row) for row in result.mappings().all()]


async def get_ticket(
    session_factory: async_sessionmaker,
    ticket_id: str,
) -> dict | None:
    async with session_factory() as session:
        query = select(tickets).where(tickets.c.ticket_id == ticket_id)
        result = await session.execute(query)
        row = result.mappings().first()
        return dict(row) if row else None