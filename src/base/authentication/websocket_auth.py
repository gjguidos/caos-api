import logging
from typing import Any

from fastapi import WebSocket, WebSocketException, status

from src.base.authentication.auth_core import check_roles_and_scopes
from src.base.services.jwt_service import JWTService
from src.base.authentication.role import Role
from src.base.entities.user import User

logger = logging.getLogger(__name__)

async def authenticate_websocket(
    websocket: WebSocket,
    token: str,
) -> dict[str, Any]:
    """Verify WebSocket JWT using the shared authentication service."""

    if not token:
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Missing authentication token",
        )

    jwt_service = websocket.app.state.jwt_service if hasattr(
        websocket.app.state, "jwt_service"
    ) else JWTService()

    claims = await jwt_service.verify_token(token)

    if not claims:
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Invalid or expired authentication token",
        )

    return claims


def check_websocket_permissions(
    user: User,
    required_roles: list[list[Role]] | None = None,
    required_scopes: list[list[str]] | None = None,
) -> None:
    """
    Enforce RBAC for WebSocket connections. Raises WebSocketException when the user
    does not satisfy the requirements.
    """
    if not check_roles_and_scopes(user.roles, user.scopes, required_roles, required_scopes):
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION, reason="Insufficient roles or scopes"
        )
