import contextlib
import logging
from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any

from fastapi import Request, WebSocket, WebSocketDisconnect, WebSocketException

from src.base.authentication.role import Role
from src.base.authentication.websocket_auth import (
    authenticate_websocket,
    check_websocket_permissions,
)
from src.base.entities.user import User
from src.base.middlewares.utils.session_utils import get_session_key

logger = logging.getLogger(__name__)

WS_POLICY_VIOLATION = 1008
WS_INTERNAL_ERROR = 1011

DEFAULT_REJECT_MESSAGE = "Invalid or missing token. Disconnecting..."
FORBIDDEN_MESSAGE = "Insufficient permissions. Disconnecting..."
SETUP_FAILURE_MESSAGE = "Connection setup failed. Disconnecting..."

WsHandler = Callable[..., Awaitable[Any]]
OnReady = Callable[[WebSocket, str], Awaitable[None]]


async def _check_authz_permissions(
    websocket: WebSocket, user: User, required_permissions: list[str]
) -> None:
    """Fetch and verify fine-grained permissions via the authz service."""
    authz_client = websocket.app.container.authz.authz_client()
    if authz_client is None:
        logger.debug(
            "[WS SESSION] Authorization service not configured - skipping permission check for user %s",
            user.id,
        )
        websocket.scope["permissions"] = set()
        return

    # Let an unreachable authz service propagate as-is: it is an outage, not a denial,
    # and the handshake must report it as an internal error rather than blame the client.
    permissions = await authz_client.get_permissions(user.access_token)

    missing = set(required_permissions) - permissions
    if missing:
        logger.warning(
            "[WS SESSION] Permission denied for user %s - missing: %s",
            user.id,
            sorted(missing),
        )
        raise PermissionError(f"Missing permissions: {sorted(missing)}")

    websocket.scope["permissions"] = permissions


async def _authenticate_and_accept(
    websocket: WebSocket,
    required_roles: list[list[Role]] | None,
    required_scopes: list[list[str]] | None,
    required_permissions: list[str] | None,
) -> str:
    """Authenticate the client, populate ``websocket.scope`` and accept the connection.

    Returns the resolved session key. Raises ``WebSocketException`` or ``PermissionError``
    when the client is legitimately rejected; any other exception is a bug, not a
    rejection, and callers should report it as such.
    """
    raw_token = websocket.headers.get("Authorization", "")
    if raw_token.startswith("Bearer "):
        jwt_token = raw_token.removeprefix("Bearer ")
    else:
        jwt_token = websocket.query_params.get("token", "")

    claims = await authenticate_websocket(websocket, jwt_token)

    user = User(
        id=claims.get("oid") or claims.get("sub"),
        email=claims.get("email") or claims.get("preferred_username"),
        name=claims.get("name"),
        roles=claims.get("roles", []),
        scopes=claims.get("scp", "").split() if claims.get("scp") else [],
        access_token=jwt_token,
    )

    if required_roles or required_scopes:
        check_websocket_permissions(
            user,
            required_roles=required_roles,
            required_scopes=required_scopes,
        )

    if required_permissions:
        await _check_authz_permissions(websocket, user, required_permissions)

    websocket.scope["signature"] = websocket.headers.get("signature") or websocket.query_params.get(
        "signature", ""
    )
    websocket.scope["user"] = user

    domain = extract_domain_from_path(websocket.url.path)
    session_key = get_session_key(jwt_token=jwt_token, domain=domain)
    websocket.scope["session_key"] = session_key
    logger.info("[🔐 WS SESSION] Session key resolved: %s", session_key)

    await websocket.accept()
    return session_key


async def _reject(websocket: WebSocket, code: int, message: str) -> None:
    """Accept, explain, then close - so the client gets both a reason and a status code.

    Each step is suppressed independently: a failed courtesy message must not stop the
    close frame that carries the actual status code.
    """
    with contextlib.suppress(Exception):
        await websocket.accept()
    with contextlib.suppress(Exception):
        await websocket.send_text(message)
    with contextlib.suppress(Exception):
        await websocket.close(code=code)


def session_based_ws(
    on_ready: OnReady | None = None,
    inject_request: bool = False,
    required_roles: list[list[Role]] | None = None,
    required_scopes: list[list[str]] | None = None,
    required_permissions: list[str] | None = None,
) -> Callable[[WsHandler], WsHandler]:
    def decorator(func: WsHandler) -> WsHandler:
        @wraps(func)
        async def wrapper(websocket: WebSocket, *args: Any, **kwargs: Any) -> Any:
            try:
                session_key = await _authenticate_and_accept(
                    websocket, required_roles, required_scopes, required_permissions
                )
            except WebSocketDisconnect:
                logger.debug("[WS SESSION] Client disconnected before the handshake completed.")
                return
            except WebSocketException as e:
                # `reason` is authored for the client in websocket_auth, so it already
                # says whether the token was missing, expired, or short on roles.
                logger.warning("[🚫 WS SESSION] Handshake rejected: %s", e.reason)
                await _reject(websocket, WS_POLICY_VIOLATION, e.reason or DEFAULT_REJECT_MESSAGE)
                return
            except PermissionError as e:
                logger.warning("[🚫 WS SESSION] Authenticated but not permitted: %s", e)
                await _reject(websocket, WS_POLICY_VIOLATION, FORBIDDEN_MESSAGE)
                return
            except Exception:
                logger.exception("[🚫 WS SESSION] Handshake failed unexpectedly.")
                await _reject(websocket, WS_INTERNAL_ERROR, SETUP_FAILURE_MESSAGE)
                return

            try:
                if on_ready:
                    await on_ready(websocket, session_key)

                if inject_request:
                    return await func(websocket, Request(websocket.scope), *args, **kwargs)

                return await func(websocket, *args, **kwargs)

            except WebSocketDisconnect:
                logger.debug("[WS SESSION] Websocket closed by client: %s", session_key)
            except Exception:
                logger.exception("[WS SESSION] Websocket handler failed: %s", session_key)
                with contextlib.suppress(Exception):
                    await websocket.close(code=WS_INTERNAL_ERROR)

        return wrapper

    return decorator


def extract_domain_from_path(path: str) -> str:
    """
    Extracts the domain segment from a URL path.

    Example:
        /api/v1/agentverse/something -> "agentverse"
        /ws/agentverse -> "agentverse"
        /agentverse/ws -> "agentverse"

    Args:
        path (str): The URL path.

    Returns:
        str: The extracted domain segment.
    """
    logger.info(f"[🔍 WS DOMAIN] Extracting domain from path: {path}")
    parts = path.strip("/").split("/")
    # Try to find the domain segment in known positions
    if "v1" in parts:
        idx = parts.index("v1")
        if len(parts) > idx + 1:
            return parts[idx + 1]
    elif len(parts) >= 2 and parts[0] == "ws":
        return parts[1]
    elif len(parts) >= 2:
        return parts[0]
    return "unknown"
