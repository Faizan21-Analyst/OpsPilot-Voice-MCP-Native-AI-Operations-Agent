from sqlalchemy import (
    MetaData,
    Table,
    Column,
    String,
    Integer
)


metadata = MetaData()


employees = Table(
    "employees",
    metadata,

    Column(
        "employee_id",
        String,
        primary_key=True,
    ),

    Column(
        "name",
        String,
        nullable=False,
    ),

    Column(
        "department",
        String,
        nullable=False,
    ),

    Column(
        "email",
        String,
        nullable=False,
    ),

    Column(
    "account_locked",
    Integer,
    nullable=False,
    server_default="0",
),
)


tickets = Table(
    "tickets",
    metadata,

    Column(
        "ticket_id",
        String,
        primary_key=True,
    ),

    Column(
        "employee_id",
        String,
        nullable=False,
    ),

    Column(
        "title",
        String,
        nullable=False,
    ),

    Column(
        "description",
        String,
        nullable=False,
    ),

    Column(
        "status",
        String,
        nullable=False,
    ),
)