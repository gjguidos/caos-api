from __future__ import annotations

import json
from typing import Any

from src.domains.browser_session.entities.models import BrowserSessionRecord


class BrowserSessionRepositoryError(RuntimeError):
    """
    Raised when browser-session persistence fails.
    """


class BrowserSessionRepository:
    """
    Cache-backed repository for temporary browser sessions.

    Browser sessions are user-scoped and expire automatically through the
    cache TTL. The repository stores internal session records, including
    Playwright storage state, and must never expose them directly to clients.
    """

    DEFAULT_KEY_PREFIX = "browser_session"

    def __init__(
        self,
        *,
        cache_repository: Any,
        key_prefix: str = DEFAULT_KEY_PREFIX,
    ) -> None:
        self._cache_repository = cache_repository
        self._key_prefix = key_prefix.rstrip(":")

    async def save(
        self,
        record: BrowserSessionRecord,
        *,
        ttl_seconds: int,
    ) -> BrowserSessionRecord:
        """
        Persist a browser-session record with a cache expiration.

        The complete record is replaced on each save.
        """

        if ttl_seconds <= 0:
            raise BrowserSessionRepositoryError(
                "Browser-session TTL must be greater than zero."
            )

        cache_key = self._build_key(
            user_id=record.user_id,
            tenant_id=record.tenant_id,
            browser_session_id=record.browser_session_id,
        )

        payload = record.model_dump_json()

        try:
            await self._cache_repository.set(
                cache_key,
                payload,
                expiration=ttl_seconds,
            )
        except Exception as exc:
            raise BrowserSessionRepositoryError(
                "Failed to save browser session."
            ) from exc

        return record

    async def get(
        self,
        *,
        user_id: str,
        browser_session_id: str,
        tenant_id: str | None = None,
    ) -> BrowserSessionRecord | None:
        """
        Retrieve a user-scoped browser session.

        Returns None when the cache key does not exist or has expired.
        """

        cache_key = self._build_key(
            user_id=user_id,
            tenant_id=tenant_id,
            browser_session_id=browser_session_id,
        )

        try:
            payload = await self._cache_repository.get(cache_key)
        except Exception as exc:
            raise BrowserSessionRepositoryError(
                "Failed to retrieve browser session."
            ) from exc

        if payload is None:
            return None

        try:
            return BrowserSessionRecord.model_validate(
                self._deserialize(payload)
            )
        except Exception as exc:
            raise BrowserSessionRepositoryError(
                "Stored browser-session data is invalid."
            ) from exc

    async def delete(
        self,
        *,
        user_id: str,
        browser_session_id: str,
        tenant_id: str | None = None,
    ) -> bool:
        """
        Remove a browser session from cache.
        """

        cache_key = self._build_key(
            user_id=user_id,
            tenant_id=tenant_id,
            browser_session_id=browser_session_id,
        )

        try:
            result = await self._cache_repository.delete(cache_key)
        except Exception as exc:
            raise BrowserSessionRepositoryError(
                "Failed to delete browser session."
            ) from exc

        if isinstance(result, bool):
            return result

        if isinstance(result, int):
            return result > 0

        return result is not None

    async def refresh(
        self,
        record: BrowserSessionRecord,
        *,
        ttl_seconds: int,
    ) -> BrowserSessionRecord:
        """
        Replace a browser-session record and refresh its cache TTL.
        """

        return await self.save(
            record,
            ttl_seconds=ttl_seconds,
        )

    async def exists(
        self,
        *,
        user_id: str,
        browser_session_id: str,
        tenant_id: str | None = None,
    ) -> bool:
        """
        Check whether a browser session currently exists.
        """

        record = await self.get(
            user_id=user_id,
            tenant_id=tenant_id,
            browser_session_id=browser_session_id,
        )
        return record is not None

    def _build_key(
        self,
        *,
        user_id: str,
        browser_session_id: str,
        tenant_id: str | None,
    ) -> str:
        """
        Build a user- and tenant-scoped cache key.
        """

        normalized_user_id = self._normalize_key_part(
            user_id,
            field_name="user_id",
        )
        normalized_session_id = self._normalize_key_part(
            browser_session_id,
            field_name="browser_session_id",
        )
        normalized_tenant_id = self._normalize_key_part(
            tenant_id or "default",
            field_name="tenant_id",
        )

        return (
            f"{self._key_prefix}:"
            f"{normalized_tenant_id}:"
            f"{normalized_user_id}:"
            f"{normalized_session_id}"
        )

    @staticmethod
    def _normalize_key_part(
        value: str,
        *,
        field_name: str,
    ) -> str:
        normalized = str(value).strip()

        if not normalized:
            raise BrowserSessionRepositoryError(
                f"{field_name} cannot be empty."
            )

        return normalized.replace(":", "_")

    @staticmethod
    def _deserialize(payload: Any) -> dict[str, Any]:
        """
        Normalize cache payloads returned as dict, string, or bytes.
        """

        if isinstance(payload, dict):
            return payload

        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")

        if isinstance(payload, str):
            decoded = json.loads(payload)

            if not isinstance(decoded, dict):
                raise TypeError(
                    "Browser-session cache payload must decode to an object."
                )

            return decoded

        raise TypeError(
            "Unsupported browser-session cache payload type: "
            f"{type(payload).__name__}."
        )