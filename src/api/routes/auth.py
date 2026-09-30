from fastapi import APIRouter, HTTPException, Request

from src.auth.models import LoginRequest, TokenResponse
from src.auth.users import get_user
from src.auth.security import verify_password, create_access_token

router = APIRouter()


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request):
    user = get_user(body.username)
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password")

    settings = request.app.state.container.settings
    token = create_access_token(user.user_id, user.role, settings.auth)

    return TokenResponse(access_token=token, user_id=user.user_id, role=user.role)