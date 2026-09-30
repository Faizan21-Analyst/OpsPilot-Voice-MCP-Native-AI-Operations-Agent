from fastapi import WebSocket, WebSocketException, status

from src.auth.security import decode_access_token, InvalidTokenError
from src.core.config import AppSettings


async def authenticate_websocket(websocket: WebSocket, settings: AppSettings) -> dict:
    """
    Extracts and validates the JWT from a WebSocket connection's query
    params, returning a principal dict. Call this before accepting the
    connection, so an invalid token is rejected before any audio/text
    starts flowing.
    """
    token = websocket.query_params.get("token")
    if not token:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="Missing token")

    try:
        payload = decode_access_token(token, settings.auth)
    except InvalidTokenError:
        raise WebSocketException(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid or expired token")

    return {"user_id": payload["user_id"], "role": payload["role"]}