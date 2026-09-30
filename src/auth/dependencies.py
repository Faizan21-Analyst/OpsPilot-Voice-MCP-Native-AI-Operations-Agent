from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from src.auth.security import decode_access_token, InvalidTokenError
from src.auth.models import TokenPayload

_bearer_scheme = HTTPBearer()


def get_current_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> TokenPayload:
    settings = request.app.state.container.settings
    try:
        payload = decode_access_token(credentials.credentials, settings.auth)
    except InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    return TokenPayload(user_id=payload["user_id"], role=payload["role"])


def require_admin(principal: TokenPayload = Depends(get_current_principal)) -> TokenPayload:
    if principal.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return principal