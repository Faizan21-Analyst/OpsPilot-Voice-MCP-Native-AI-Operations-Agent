import asyncio

from src.mcp_servers.sql.tools import (
    get_employee,
    search_employees,
    get_tickets_for_employee,
    get_ticket,
)


async def main():

    print(
        await get_employee("E001")
    )

    print(
        await search_employees(
            department="Engineering"
        )
    )

    print(
        await get_tickets_for_employee(
            employee_id="E001",
            status="OPEN",
        )
    )

    print(
        await get_ticket("T001")
    )


asyncio.run(main())