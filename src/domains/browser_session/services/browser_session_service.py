from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from src.base.events.models import DomainEvent
from src.base.events.publisher import DomainEventPublisher
from src.domains.browser_session.services.browser_session_bootstrapper import (
    BrowserSessionBootstrapError,
    BrowserSessionBootstrapper,
)
from src.domains.browser_session.store.browser_session_store import (
    BrowserSessionStore,
    BrowserSessionStoreError,
)

logger = logging.getLogger(__name__)


class BrowserSessionServiceError(RuntimeError):
    """
    Raised when a browser-session operation cannot be completed.
    """


class BrowserSessionService:
    """
    Application service for temporary interactive browser sessions.

    Responsibilities:
    - start interactive authentication sessions;
    - receive authenticated runner callbacks;
    - store safe session metadata;
    - resolve runner sessions for trusted backend consumers;
    - render pages through the authenticated browser runner;
    - close runner sessions and delete cached metadata.
    """

    def __init__(
        self,
        *,
        store: BrowserSessionStore,
        bootstrapper: BrowserSessionBootstrapper,
        event_publisher: DomainEventPublisher,
        browser_session_callback_url: str | None = None,
        browser_session_callback_token: str | None = None,
        default_ttl_seconds: int = 1800,
    ) -> None:
        self._store = store
        self._bootstrapper = bootstrapper
        self._event_publisher = event_publisher
        self._browser_session_callback_url = browser_session_callback_url
        self._browser_session_callback_token = browser_session_callback_token
        self._default_ttl_seconds = default_ttl_seconds


    async def start_session(
        self,
        *,
        user_id: str,
        target_url: str,
        success_url_contains: str | None = None,
        tenant_id: str | None = None,
        browser_session_id: str | None = None,
        ttl_seconds: int | None = None,
        headed: bool = True,
    ) -> dict[str, Any]:
        # The remote runner owns the headed browser and login flow.
        del headed

        if not user_id:
            raise BrowserSessionServiceError(
                "user_id cannot be empty."
            )
        
        if not self._browser_session_callback_url:
            raise BrowserSessionServiceError(
                "Browser-session callback URL is not configured."
            )

        domain = self._domain_from_url(target_url)
        resolved_session_id = (
            browser_session_id or self._new_session_id()
        )
        ttl = (
            self._default_ttl_seconds
            if ttl_seconds is None
            else int(ttl_seconds)
        )

        if ttl <= 0:
            raise BrowserSessionServiceError(
                "ttl_seconds must be greater than zero."
            )

        runner_session_id: str | None = None

        try:
            started = await self._bootstrapper.start(
                target_url=target_url,
                agentverse_browser_session_id=resolved_session_id,
                success_url_contains=success_url_contains,
                callback_url=self._browser_session_callback_url,
                callback_token=self._browser_session_callback_token,
                auto_capture=True,
            )

            runner_session_id = started.runner_session_id

            if not runner_session_id:
                logger.warning(
                    "Saving ready browser session without runner_session_id.",
                    extra={
                        "browser_session_id": browser_session_id,
                        "user_id": user_id,
                        "tenant_id": tenant_id,
                    },
                )

            metadata = {
                "ok": True,
                "ready": False,
                "status": "waiting_for_login",
                "browser_session_id": resolved_session_id,
                "runner_session_id": runner_session_id,
                "domain": domain,
                "target_url": started.target_url,
                "final_url": started.final_url,
                "interactive_url": started.interactive_url,
                "success_url_contains": success_url_contains,
                "tenant_id": tenant_id,
                "ttl_seconds": ttl,
                "secrets_redacted": True,
            }

            await self._store.save_metadata(
                user_id=user_id,
                browser_session_id=resolved_session_id,
                metadata=metadata,
                tenant_id=tenant_id,
                ttl_seconds=ttl,
            )

            return self._to_public_metadata(metadata)

        except (
            BrowserSessionBootstrapError,
            BrowserSessionStoreError,
        ) as exc:
            if runner_session_id:
                try:
                    await self._bootstrapper.delete(
                        runner_session_id=runner_session_id,
                    )
                except BrowserSessionBootstrapError:
                    pass

            raise BrowserSessionServiceError(str(exc)) from exc
        
    async def handle_auto_capture_callback(
        self,
        *,
        payload: Any,
        callback_token: str | None,
    ) -> dict[str, Any]:
        logger.info("Received browser-session callback. event=%s browser_session_id=%s", payload.event, payload.browser_session_id)
        if self._browser_session_callback_token:
            if callback_token != self._browser_session_callback_token:
                raise BrowserSessionServiceError(
                    "Invalid browser-session callback token."
                )

        if payload.event != "browser_session.authenticated":
            raise BrowserSessionServiceError(
                f"Unsupported browser-session callback event: {payload.event}"
            )

        browser_session_id = payload.browser_session_id

        if not browser_session_id:
            raise BrowserSessionServiceError(
                "Callback payload is missing browser_session_id."
            )

        identity = await self._store.get_identity_by_browser_session_id(
            browser_session_id=browser_session_id,
        )

        if identity is None:
            raise BrowserSessionServiceError(
                f"Browser session not found: {browser_session_id}"
            )

        user_id = str(identity["user_id"])
        tenant_id = identity.get("tenant_id")

        metadata = await self._store.get_status(
            user_id=user_id,
            browser_session_id=browser_session_id,
            tenant_id=tenant_id,
        )

        if metadata is None:
            raise BrowserSessionServiceError(
                f"Browser session metadata not found: {browser_session_id}"
            )

        ttl = int(
            metadata.get("ttl_seconds")
            or self._default_ttl_seconds
        )

        if ttl <= 0:
            raise BrowserSessionServiceError(
                "Stored browser-session TTL is invalid."
            )


        target_url = (
            payload.target_url
            or metadata.get("target_url")
        )

        if not target_url:
            raise BrowserSessionServiceError(
                "Browser-session callback cannot resolve target_url."
            )
        
        logger.info("Browser-session callback target_url=%s, browser_session_id=%s, user_id=%s", target_url, browser_session_id, user_id)

        final_url = str(payload.final_url or "")

        if not final_url:
            raise BrowserSessionServiceError(
                "Browser-session callback cannot resolve final_url."
            )
        
        observed_domain = str(
            payload.domain
            or self._domain_from_url(final_url)
        ).lower()

        expected_domain = str(
            metadata.get("domain")
            or self._domain_from_url(str(target_url))
        ).lower()

        domain=expected_domain

        final_hostname = (
            urlparse(final_url).hostname or ""
        ).lower()

        expected_domain = str(domain or "").lower()

        success_url_contains = metadata.get(
            "success_url_contains"
        )

        if success_url_contains:
            authentication_succeeded = (
                str(success_url_contains).lower()
                in final_url.lower()
            )
        else:
            authentication_succeeded = (
                final_hostname == expected_domain
                or final_hostname.endswith(
                    f".{expected_domain}"
                )
            )

        if not authentication_succeeded:
            raise BrowserSessionServiceError(
                "Browser-session callback was received before authentication completed. "
                f"Current browser URL is: {final_url}"
            )

        updated_metadata = {
            **metadata,
            "ok": True,
            "status": "authenticated",
            "ready": True,
            "browser_session_id": browser_session_id,
            "user_id": user_id,
            "tenant_id": tenant_id,
            "domain": expected_domain,
            "observed_domain": observed_domain,
            "target_url": str(target_url),
            "final_url": payload.final_url or metadata.get("final_url"),
            "interactive_url": None,
            "cookies_count": payload.cookies_count,
            "matching_cookie_count": payload.matching_cookie_count,
            "origins_count": payload.origins_count,
            "ttl_seconds": ttl,
            "secrets_redacted": True,
        }

        await self._store.save_metadata(
            user_id=user_id,
            browser_session_id=browser_session_id,
            metadata=updated_metadata,
            tenant_id=tenant_id,
            ttl_seconds=ttl,
        )

        try:
            await self._event_publisher.publish(
                DomainEvent(
                    event_type="browser_session.authenticated",
                    source="browser_session",
                    user_id=user_id,
                    tenant_id=tenant_id,
                    correlation_id=browser_session_id,
                    data={
                        "browser_session_id": browser_session_id,
                        "status": "authenticated",
                        "ready": True,
                        "domain": str(domain),
                        "target_url": str(target_url),
                        "final_url": payload.final_url,
                        "secrets_redacted": True,
                    },
                )
            )

        except Exception:
            logger.exception(
                "Failed to publish browser-session authenticated event: "
                "browser_session_id=%s user_id=%s",
                browser_session_id,
                user_id,
            )

        target_url = updated_metadata.get("target_url")
        domain = updated_metadata.get("domain")
        ready = updated_metadata.get("ready")
        status = updated_metadata.get("status")
        metadata_keys = list(updated_metadata.keys())

        logger.info(
            f"Browser-session callback handled. browser_session_id={browser_session_id}, user_id={user_id}, target_url={target_url}, domain={domain}, ready={ready}, status={status}, metadata_keys={metadata_keys}",
            extra={
                "browser_session_id": browser_session_id,
                "user_id": user_id,
                "target_url": updated_metadata.get("target_url"),
                "domain": updated_metadata.get("domain"),
                "ready": updated_metadata.get("ready"),
                "status": updated_metadata.get("status"),
                "metadata_keys": list(updated_metadata.keys()),
            },
        )

        return self._to_public_metadata(updated_metadata)

    async def get_status(
        self,
        *,
        user_id: str,
        browser_session_id: str,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            metadata = await self._store.get_status(
                user_id=user_id,
                browser_session_id=browser_session_id,
                tenant_id=tenant_id,
            )

            if metadata is None:
                return {
                    "ok": True,
                    "ready": False,
                    "status": None,
                    "browser_session_id": browser_session_id,
                    "user_id": user_id,
                    "tenant_id": tenant_id,
                    "secrets_redacted": True,
                }

            return self._to_public_metadata(metadata)

        except BrowserSessionStoreError as exc:
            raise BrowserSessionServiceError(str(exc)) from exc

    async def render_page(
        self,
        *,
        user_id: str,
        browser_session_id: str,
        url: str,
        tenant_id: str | None = None,
        wait_until: str = "domcontentloaded",
        wait_ms: int = 2500,
        max_html_bytes: int = 5_000_000,
    ) -> dict[str, Any]:
        metadata = await self._store.get_status(
            user_id=user_id,
            browser_session_id=browser_session_id,
            tenant_id=tenant_id,
        )

        if not metadata:
            raise BrowserSessionServiceError(
                f"Browser session not found: {browser_session_id}"
            )

        if not metadata.get("ready"):
            raise BrowserSessionServiceError(
                f"Browser session is not ready: {browser_session_id}"
            )

        expected_domain = str(metadata.get("domain") or "").lower()

        if not expected_domain:
            raise BrowserSessionServiceError(
                f"Browser session has no expected domain: {browser_session_id}"
            )

        if not self._is_allowed_domain(
            requested_url=url,
            expected_domain=expected_domain,
        ):
            raise BrowserSessionServiceError(
                f"Render URL is outside browser-session domain: {url}"
            )

        runner_session_id = metadata.get("runner_session_id")

        if not runner_session_id:
            raise BrowserSessionServiceError(
                f"Browser session has no runner_session_id: {browser_session_id}"
            )

        try:
            rendered = await self._bootstrapper.render_page(
                runner_session_id=str(runner_session_id),
                url=url,
                wait_until=wait_until,
                wait_ms=wait_ms,
                max_html_bytes=max_html_bytes,
            )

            return rendered

        except BrowserSessionBootstrapError as exc:
            ttl = int(
                metadata.get("ttl_seconds")
                or self._default_ttl_seconds
            )

            await self._store.save_metadata(
                user_id=user_id,
                browser_session_id=browser_session_id,
                metadata={
                    **metadata,
                    "ready": False,
                    "status": "runner_session_unavailable",
                    "error": str(exc),
                    "secrets_redacted": True,
                },
                tenant_id=tenant_id,
                ttl_seconds=ttl,
            )

            raise BrowserSessionServiceError(str(exc)) from exc
    
    def _is_allowed_domain(
        self,
        *,
        requested_url: str,
        expected_domain: str,
    ) -> bool:
        requested_domain = self._domain_from_url(requested_url)
        expected = expected_domain.lstrip(".").lower()

        return (
            requested_domain == expected
            or requested_domain.endswith(f".{expected}")
        )

    async def resolve_runner_session_id(
        self,
        *,
        user_id: str,
        browser_session_id: str,
        tenant_id: str | None = None,
    ) -> str:
        metadata = await self._store.get_status(
            user_id=user_id,
            browser_session_id=browser_session_id,
            tenant_id=tenant_id,
        )

        if not metadata:
            raise BrowserSessionServiceError(
                f"Browser session not found: {browser_session_id}"
            )

        if not metadata.get("ready"):
            raise BrowserSessionServiceError(
                f"Browser session is not ready: {browser_session_id}"
            )

        runner_session_id = metadata.get("runner_session_id")

        if not runner_session_id:
            raise BrowserSessionServiceError(
                f"Browser session has no runner_session_id: {browser_session_id}"
            )

        return str(runner_session_id)

    async def delete_session(
        self,
        *,
        user_id: str,
        browser_session_id: str,
        tenant_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            metadata = await self._store.get_status(
                user_id=user_id,
                browser_session_id=browser_session_id,
                tenant_id=tenant_id,
            )

            runner_session_id = (
                metadata.get("runner_session_id")
                if metadata
                else None
            )

            runner_deleted = False

            if runner_session_id:
                try:
                    runner_deleted = await self._bootstrapper.delete(
                        runner_session_id=str(runner_session_id),
                    )
                except BrowserSessionBootstrapError as exc:
                    logger.warning(
                        "Runner session could not be deleted: "
                        "browser_session_id=%s runner_session_id=%s error=%s",
                        browser_session_id,
                        runner_session_id,
                        exc,
                    )

            await self._store.delete(
                user_id=user_id,
                browser_session_id=browser_session_id,
                tenant_id=tenant_id,
            )

            return {
                "ok": True,
                "deleted": True,
                "runner_deleted": runner_deleted,
                "browser_session_id": browser_session_id,
                "user_id": user_id,
                "tenant_id": tenant_id,
                "secrets_redacted": True,
            }

        except BrowserSessionStoreError as exc:
            raise BrowserSessionServiceError(str(exc)) from exc

    @staticmethod
    def _domain_from_url(
        target_url: str,
    ) -> str:
        parsed = urlparse(target_url)
        domain = parsed.hostname

        if not domain:
            raise BrowserSessionServiceError(
                "target_url must include a valid domain."
            )

        return domain.lower()

    @staticmethod
    def _new_session_id() -> str:
        return f"bs_{uuid4().hex}"
    
    @staticmethod
    def _to_public_metadata(
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        public = dict(metadata)
        public.pop("runner_session_id", None)
        return public