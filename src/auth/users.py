"""
Dummy user store for the demo. Passwords are bcrypt-hashed even here,
since "dummy" shouldn't mean "plaintext" - the hashing code path is the
same one that would back real accounts later.
"""
from dataclasses import dataclass

from src.auth.security import hash_password


@dataclass
class DummyUser:
    user_id: str
    username: str
    password_hash: str
    role: str  # "admin" | "employee"


_USERS: dict[str, DummyUser] = {
    "admin": DummyUser(
        user_id="E999",
        username="admin",
        password_hash=hash_password("admin123"),
        role="admin",
    ),
    "employee": DummyUser(
        user_id="E001",
        username="employee",
        password_hash=hash_password("employee123"),
        role="employee",
    ),
}


def get_user(username: str) -> DummyUser | None:
    return _USERS.get(username)