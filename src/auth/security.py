from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from src.core.config import AuthSettings
from src.core.exceptions import PlatformError


class InvalidTokenError(PlatformError):
    code = "invalid_token"


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_access_token(user_id: str, role: str, settings: AuthSettings) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.token_expire_minutes)
    payload = {"user_id": user_id, "role": role, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_access_token(token: str, settings: AuthSettings) -> dict:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as e:
        raise InvalidTokenError(f"Invalid or expired token: {e}") from e