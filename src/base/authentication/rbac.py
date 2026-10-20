import logging

from fastapi import HTTPException, Request, status

from src.base.authentication.auth_core import check_roles_and_scopes
from src.base.authentication.role import Role
from src.base.entities.user import User

logger = logging.getLogger(__name__)


async def _check_permissions(request: Request, user: User, required_permissions: list[str]):
    """Fetch and verify fine-grained permissions via the authz service."""
    authz_client = request.app.container.authz.authz_client()
    if authz_client is None:
        logger.debug(
            "Authorization service not configured - skipping permission check for user %s",
            user.id,
        )
        request.state.permissions = set()
        return

    if not getattr(user, "access_token", None):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing access token",
        )

    try:
        permissions = await authz_client.get_permissions(user.access_token)
    except Exception:
        logger.exception("Failed to fetch permissions for user %s", user.id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authorization service unavailable",
        )

    request.state.permissions = permissions

    missing = set(required_permissions) - permissions
    if missing:
        logger.warning(
            "Permission denied for user %s - required: %s, missing: %s",
            user.id,
            required_permissions,
            sorted(missing),
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Forbidden: missing permissions {sorted(missing)}",
        )


def require_roles_and_scopes(
    required_roles: list[list[Role]] = None,
    required_scopes: list[list[str]] = None,
    required_permissions: list[str] = None,
):
    """
    Dependency for FastAPI endpoints that enforces RBAC and optionally
    fine-grained permissions via the authz service.

    When ``required_permissions`` is provided and ``AUTHZ_ENABLED=true``,
    the user's permissions are fetched from the user-management service
    and checked.  When ``AUTHZ_ENABLED=false``, permission checks are
    skipped while role/scope checks still apply.
    """

    async def checker(request: Request) -> User:
        user = getattr(request.state, "user", None)
        if user is None:
            logger.warning(
                "Authentication failed - missing token. Required roles: %s, Required scopes: %s",
                required_roles,
                required_scopes,
            )
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")

        if not check_roles_and_scopes(user.roles, user.scopes, required_roles, required_scopes):
            logger.warning(
                "Authorization failed - insufficient roles or scopes. User roles: %s, Required roles: %s, User scopes: %s, Required scopes: %s",
                user.roles,
                required_roles,
                user.scopes,
                required_scopes,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Forbidden: insufficient roles or scopes",
            )

        if required_permissions:
            await _check_permissions(request, user, required_permissions)

        return user

    return checker
