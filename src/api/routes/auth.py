from fastapi import APIRouter, HTTPException, Request

from src.auth.models import LoginRequest, TokenResponse
from src.auth.users import get_user
from src.auth.security import verify_password, create_access_token
from src.core.logging import get_logger
from src.portal import accounts
from src.portal.common import ServiceError

router = APIRouter()
log = get_logger(__name__)


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request):
    settings = request.app.state.container.settings
    session_factory = getattr(request.app.state, "session_factory", None)
    ip = request.client.host if request.client else None

    who = None
    if session_factory is not None:
        try:
            # Database-backed accounts: hashed passwords, failed-attempt counting, escalating lockouts.
            who = await accounts.authenticate(session_factory, body.username, body.password, ip)
        except ServiceError as e:
            raise HTTPException(status_code=e.status, detail=e.message)
        except Exception as e:  # database problem: fall back to the original demo users so login never breaks
            log.error("login_db_failed_using_legacy_users", error=str(e))

    if who is None:
        user = get_user(body.username)
        if user is None or not verify_password(body.password, user.password_hash):
            raise HTTPException(status_code=401, detail="Invalid username or password")
        who = {"user_id": user.user_id, "role": user.role}

    token = create_access_token(who["user_id"], who["role"], settings.auth)
    return TokenResponse(access_token=token, user_id=who["user_id"], role=who["role"])
