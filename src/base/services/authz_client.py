from __future__ import annotations

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from typing import Literal

from cachetools import TTLCache

from src.base.infrastructure.http.async_http_client import AsyncHttpClient, HttpClientError
from src.base.infrastructure.http.http_client_config import HttpClientConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuthzResult:
    allowed: bool
    role: Literal["admin", "user", "superadmin"] | None


class AuthzClient:
    """
    Client for the User Management service /authz/permissions endpoint.

    Maintains an in-process TTLCache keyed on hashed access tokens to minimize live HTTP calls for repeated permission
    checks within the TTL window.
    """

    def __init__(self, base_url: str, timeout: float = 10.0, cache_ttl: int = 60):
        self._base_url = base_url.rstrip("/")
        self._http = AsyncHttpClient(
            config=HttpClientConfig(
                base_url=self._base_url,
                timeout=timeout,
                max_retries=1,
                follow_redirects=True,
                verify_ssl=True,
            )
        )
        self._cache: TTLCache = TTLCache(maxsize=4096, ttl=cache_ttl)
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_permissions(self, access_token: str) -> set[str]:
        """
        Fetch all permissions for the user identified by the bearer token.

        Calls GET /v1/authz/permissions on the user management service.
        Results are cached by token hash for the configured TTL.
        """
        cache_key = ("permissions", hashlib.sha256(access_token.encode()).hexdigest())

        async with self._lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached

        headers = {"Authorization": f"Bearer {access_token}"}
        try:
            data = await self._http.get("/v1/authz/permissions", headers=headers)
            permissions = set(data.get("permissions", []))
        except HttpClientError as exc:
            logger.error("authz/permissions call failed: %s", exc)
            raise

        async with self._lock:
            self._cache[cache_key] = permissions

        return permissions
