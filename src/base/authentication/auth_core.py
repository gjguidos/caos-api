"""
JWT Authentication module.
"""

import asyncio
import logging
import time
from typing import Any

import httpx
from jose import JWTError, jwt

from src.base.authentication.role import Role
from src.base.config.config import settings

logger = logging.getLogger(__name__)

# Configuration from settings
AUDIENCE = settings.azure.entra_client_id
ISSUER = settings.azure.entra_issuer
JWKS_URL = settings.azure.entra_jwks_url

_JWKS_TTL = 86400  # 24 hours in seconds
_JWKS_TIMEOUT = 10  # seconds before giving up on the fetch

_jwks_cache: dict[str, Any] | None = None
_jwks_fetched_at: float = 0.0
_jwks_lock = asyncio.Lock()


async def _fetch_jwks() -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=_JWKS_TIMEOUT) as client:
        response = await client.get(JWKS_URL)
        response.raise_for_status()
        return response.json()


async def get_jwks(force_refresh: bool = False) -> dict[str, Any]:
    """
    Returns the cached JWKS, refreshing from the remote endpoint if the cache
    has expired or a forced refresh is requested (e.g. on a key-rotation miss).
    The lock prevents thundering-herd on concurrent refreshes.
    """
    global _jwks_cache, _jwks_fetched_at
    async with _jwks_lock:
        cache_age = time.monotonic() - _jwks_fetched_at
        if not force_refresh and _jwks_cache and cache_age < _JWKS_TTL:
            return _jwks_cache
        logger.info("Fetching JWKS from %s (force_refresh=%s)", JWKS_URL, force_refresh)
        _jwks_cache = await _fetch_jwks()
        _jwks_fetched_at = time.monotonic()
        return _jwks_cache


async def validate_jwt_token(token: str) -> dict[str, Any]:
    """
    Validates a JWT and returns claims (raises JWTError/ExpiredSignatureError if invalid).
    On a signing-key miss the JWKS cache is refreshed once before failing, so
    key rotations on the Entra side are handled without a service restart.
    """
    jwks = await get_jwks()
    unverified_header = jwt.get_unverified_header(token)

    kid = unverified_header.get("kid")
    key = next((k for k in jwks["keys"] if k["kid"] == kid), None)

    if not key:
        # kid not in cache - Entra may have rotated keys; refresh and retry once
        logger.info("Signing key %r not found in JWKS cache, refreshing", kid)
        jwks = await get_jwks(force_refresh=True)
        key = next((k for k in jwks["keys"] if k["kid"] == kid), None)
        if not key:
            raise JWTError("Invalid signing key")

    payload = jwt.decode(token, key, algorithms=["RS256"], audience=AUDIENCE, issuer=ISSUER)
    return payload


def check_roles_and_scopes(
    user_roles: list[str],
    user_scopes: list[str],
    required_roles: list[list[Role]] | None = None,
    required_scopes: list[list[str]] | None = None,
) -> bool:
    """
    Check if claims contain at least one valid combination of roles and scopes.
    """
    required_roles = required_roles or []
    required_scopes = required_scopes or []

    # Convert Role enums to string values for comparison with user_roles
    roles_ok = not required_roles or any(
        all(r.value in user_roles for r in group) for group in required_roles
    )
    scopes_ok = not required_scopes or any(
        all(s in user_scopes for s in group) for group in required_scopes
    )

    return roles_ok and scopes_ok
