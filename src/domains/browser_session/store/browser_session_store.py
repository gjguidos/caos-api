from __future__ import annotations

import logging
import json
from datetime import UTC, datetime
from typing import Any

from src.domains.browser_session.helpers.cache_helpers import (
    browser_session_meta_key,
    browser_session_state_key,
)


logger = logging.getLogger(__name__)

class BrowserSessionStoreError(RuntimeError):
    pass


class BrowserSessionStore:
    """
    Redis-backed storage for authenticated browser sessions.

    Safe metadata and sensitive Playwright storage state are stored under
    separate Redis keys.
    """

    def __init__(
        self,
        *,
        cache_repository: Any,
        redis_prefix: str,
        redis_key_ns: str,
        key_version: int = 1,
        default_ttl_seconds: int = 1800,
        multi_tenant: bool = False,
    ) -> None:
        self._repo = cache_repository
        self._redis_prefix = redis_prefix.rstrip(":")
        self._redis_key_ns = redis_key_ns
        self._key_version = key_version
        self._default_ttl_seconds = default_ttl_seconds
        self._multi_tenant = multi_tenant

    async def get_status_by_browser_session_id(
        self,
        *,
        browser_session_id: str,
    ) -> dict[str, Any] | None:
        if not browser_session_id:
            raise BrowserSessionStoreError(
                "browser_session_id cannot be empty."
            )

        raw_index = await self._repo.get(
            self._index_key(
                browser_session_id=browser_session_id,
            )
        )

        if raw_index is None:
            return None

        index = self._decode_json(raw_index)

        if not isinstance(index, dict):
            raise BrowserSessionStoreError(
                "Stored browser-session index is invalid."
            )

        user_id = index.get("user_id")
        tenant_id = index.get("tenant_id")

        if not isinstance(user_id, str) or not user_id:
            raise BrowserSessionStoreError(
                "Stored browser-session index is missing user_id."
            )

        if tenant_id is not None and not isinstance(tenant_id, str):
            raise BrowserSessionStoreError(
                "Stored browser-session index has invalid tenant_id."
            )

        return await self.get_status(
            user_id=user_id,
            browser_session_id=browser_session_id,
            tenant_id=tenant_id,
        )
    
    async def get_identity_by_browser_session_id(
        self,
        *,
        browser_session_id: str,
    ) -> dict[str, str | None] | None:
        if not browser_session_id:
            raise BrowserSessionStoreError(
                "browser_session_id cannot be empty."
            )

        raw_index = await self._repo.get(
            self._index_key(
                browser_session_id=browser_session_id,
            )
        )

        if raw_index is None:
            return None

        index = self._decode_json(raw_index)

        if not isinstance(index, dict):
            raise BrowserSessionStoreError(
                "Stored browser-session index is invalid."
            )

        user_id = index.get("user_id")
        tenant_id = index.get("tenant_id")

        if not isinstance(user_id, str) or not user_id:
            raise BrowserSessionStoreError(
                "Stored browser-session index is missing user_id."
            )

        if tenant_id is not None and not isinstance(tenant_id, str):
            raise BrowserSessionStoreError(
                "Stored browser-session index has invalid tenant_id."
            )

        return {
            "user_id": user_id,
            "tenant_id": tenant_id,
        }

    async def save_storage_state(
        self,
        *,
        user_id: str,
        browser_session_id: str,
        storage_state: dict[str, Any],
        domain: str,
        tenant_id: str | None = None,
        ttl_seconds: int | None = None,
    ) -> dict[str, Any]:
        self._validate_identity(
            user_id=user_id,
            browser_session_id=browser_session_id,
        )

        if not domain:
            raise BrowserSessionStoreError(
                "domain cannot be empty."
            )

        self._validate_storage_state(storage_state)

        ttl = (
            ttl_seconds
            if ttl_seconds is not None
            else self._default_ttl_seconds
        )

        if ttl <= 0:
            raise BrowserSessionStoreError(
                "ttl_seconds must be greater than zero."
            )

        state_key = self._state_key(
            user_id=user_id,
            browser_session_id=browser_session_id,
            tenant_id=tenant_id,
        )
        
        meta_key = self._meta_key(
            user_id=user_id,
            browser_session_id=browser_session_id,
            tenant_id=tenant_id,
        )

        now = datetime.now(UTC)

        existing_metadata = await self.get_status(
            user_id=user_id,
            browser_session_id=browser_session_id,
            tenant_id=tenant_id,
        )

        logger.info(f"State key {state_key} will expire in {ttl} seconds at {now.timestamp() + ttl}")
        metadata = {
            **(existing_metadata or {}),
            "browser_session_id": browser_session_id,
            "domain": domain,
            "user_id": user_id,
            "tenant_id": tenant_id,
            "created_at": (existing_metadata or {}).get("created_at") or now.isoformat(),
            "authenticated_at": now.isoformat(),
            "ttl_seconds": ttl,
            "ready": True,
            "status": "ready",
            "secrets_redacted": True,
        }

        await self._repo.set(
            state_key,
            json.dumps(storage_state),
            expiration=ttl,
        )

        try:
            await self._repo.set(
                meta_key,
                json.dumps(metadata),
                expiration=ttl,
            )

            await self._save_index(
                user_id=user_id,
                browser_session_id=browser_session_id,
                tenant_id=tenant_id,
                ttl_seconds=ttl,
            )
        except Exception:
            # Prevent secret state without matching metadata.
            await self._repo.delete(state_key)
            await self._repo.delete(meta_key)
            await self._repo.delete(
                self._index_key(
                    browser_session_id=browser_session_id,
                )
            )
            raise

        return metadata
    
    def _index_key(
        self,
        *,
        browser_session_id: str,
    ) -> str:
        return (
            f"{self._redis_prefix}:"
            f"{self._redis_key_ns}:"
            f"v{self._key_version}:"
            f"browser_session:index:"
            f"{browser_session_id}"
        )
    
    async def _save_index(
        self,
        *,
        user_id: str,
        browser_session_id: str,
        tenant_id: str | None,
        ttl_seconds: int,
    ) -> None:
        await self._repo.set(
            self._index_key(
                browser_session_id=browser_session_id,
            ),
            json.dumps(
                {
                    "user_id": user_id,
                    "tenant_id": tenant_id,
                    "browser_session_id": browser_session_id,
                }
            ),
            expiration=ttl_seconds,
        )

    async def save_metadata(
        self,
        *,
        user_id: str,
        browser_session_id: str,
        metadata: dict[str, Any],
        tenant_id: str | None = None,
        ttl_seconds: int | None = None,
    ) -> dict[str, Any]:
        """
        Save safe metadata before authentication has been captured.
        """

        self._validate_identity(
            user_id=user_id,
            browser_session_id=browser_session_id,
        )

        ttl = (
            ttl_seconds
            if ttl_seconds is not None
            else self._default_ttl_seconds
        )

        safe_metadata = {
            **metadata,
            "browser_session_id": browser_session_id,
            "user_id": user_id,
            "tenant_id": tenant_id,
            "secrets_redacted": True,
        }

        await self._repo.set(
            self._meta_key(
                user_id=user_id,
                browser_session_id=browser_session_id,
                tenant_id=tenant_id,
            ),
            json.dumps(safe_metadata),
            expiration=ttl,
        )

        await self._save_index(
            user_id=user_id,
            browser_session_id=browser_session_id,
            tenant_id=tenant_id,
            ttl_seconds=ttl,
        )

        return safe_metadata

    async def get_storage_state(
        self,
        *,
        user_id: str,
        browser_session_id: str,
        tenant_id: str | None = None,
    ) -> dict[str, Any] | None:
        self._validate_identity(
            user_id=user_id,
            browser_session_id=browser_session_id,
        )

        raw = await self._repo.get(
            self._state_key(
                user_id=user_id,
                browser_session_id=browser_session_id,
                tenant_id=tenant_id,
            )
        )

        if raw is None:
            return None

        value = self._decode_json(raw)
        self._validate_storage_state(value)

        return value

    async def get_status(
        self,
        *,
        user_id: str,
        browser_session_id: str,
        tenant_id: str | None = None,
    ) -> dict[str, Any] | None:
        self._validate_identity(
            user_id=user_id,
            browser_session_id=browser_session_id,
        )

        raw = await self._repo.get(
            self._meta_key(
                user_id=user_id,
                browser_session_id=browser_session_id,
                tenant_id=tenant_id,
            )
        )

        if raw is None:
            return None

        metadata = self._decode_json(raw)

        if not isinstance(metadata, dict):
            raise BrowserSessionStoreError(
                "Stored browser-session metadata is invalid."
            )

        # Never allow accidentally persisted secrets into status output.
        metadata.pop("storage_state", None)
        metadata.pop("cookies", None)
        metadata.pop("origins", None)

        return {
            **metadata,
            "secrets_redacted": True,
        }

    async def refresh(
        self,
        *,
        user_id: str,
        browser_session_id: str,
        tenant_id: str | None = None,
        ttl_seconds: int | None = None,
    ) -> bool:
        ttl = (
            ttl_seconds
            if ttl_seconds is not None
            else self._default_ttl_seconds
        )

        state_key = self._state_key(
            user_id=user_id,
            browser_session_id=browser_session_id,
            tenant_id=tenant_id,
        )

        meta_key = self._meta_key(
            user_id=user_id,
            browser_session_id=browser_session_id,
            tenant_id=tenant_id,
        )
        index_key = self._index_key(
            browser_session_id=browser_session_id,
        )

        state_exists = await self._repo.exists(state_key)
        meta_exists = await self._repo.exists(meta_key)
        index_exists = await self._repo.exists(index_key)

        if state_exists:
            await self._repo.expire(state_key, ttl)

        if meta_exists:
            await self._repo.expire(meta_key, ttl)

        if index_exists:
            await self._repo.expire(index_key, ttl)

        return bool(state_exists or meta_exists or index_exists)

    async def delete(
        self,
        *,
        user_id: str,
        browser_session_id: str,
        tenant_id: str | None = None,
    ) -> None:
        self._validate_identity(
            user_id=user_id,
            browser_session_id=browser_session_id,
        )

        await self._repo.delete(
            self._state_key(
                user_id=user_id,
                browser_session_id=browser_session_id,
                tenant_id=tenant_id,
            )
        )

        await self._repo.delete(
            self._meta_key(
                user_id=user_id,
                browser_session_id=browser_session_id,
                tenant_id=tenant_id,
            )
        )

        await self._repo.delete(
            self._index_key(
                browser_session_id=browser_session_id,
            )
        )

    def _state_key(
        self,
        *,
        user_id: str,
        browser_session_id: str,
        tenant_id: str | None,
    ) -> str:
        return browser_session_state_key(
            redis_prefix=self._redis_prefix,
            redis_key_ns=self._redis_key_ns,
            key_version=self._key_version,
            multi_tenant=self._multi_tenant,
            user_id=user_id,
            browser_session_id=browser_session_id,
            tenant_id=tenant_id,
        )

    def _meta_key(
        self,
        *,
        user_id: str,
        browser_session_id: str,
        tenant_id: str | None,
    ) -> str:
        return browser_session_meta_key(
            redis_prefix=self._redis_prefix,
            redis_key_ns=self._redis_key_ns,
            key_version=self._key_version,
            multi_tenant=self._multi_tenant,
            user_id=user_id,
            browser_session_id=browser_session_id,
            tenant_id=tenant_id,
        )

    @staticmethod
    def _validate_identity(
        *,
        user_id: str,
        browser_session_id: str,
    ) -> None:
        if not user_id:
            raise BrowserSessionStoreError(
                "user_id cannot be empty."
            )

        if not browser_session_id:
            raise BrowserSessionStoreError(
                "browser_session_id cannot be empty."
            )

    @staticmethod
    def _decode_json(raw: Any) -> Any:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")

        if not isinstance(raw, str):
            raise BrowserSessionStoreError(
                "Stored browser-session value must be a JSON string."
            )

        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise BrowserSessionStoreError(
                "Stored browser-session value is not valid JSON."
            ) from exc

    @staticmethod
    def _validate_storage_state(
        storage_state: Any,
    ) -> None:
        if not isinstance(storage_state, dict):
            raise BrowserSessionStoreError(
                "Playwright storage_state must be a dict."
            )

        if not isinstance(storage_state.get("cookies", []), list):
            raise BrowserSessionStoreError(
                "storage_state.cookies must be a list."
            )

        if not isinstance(storage_state.get("origins", []), list):
            raise BrowserSessionStoreError(
                "storage_state.origins must be a list."
            )