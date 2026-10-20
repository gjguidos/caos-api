from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, HttpUrl, field_validator


class BrowserSessionStatus(StrEnum):
    """
    Lifecycle states for a temporary browser authentication session.
    """

    STARTING = "starting"
    WAITING_FOR_LOGIN = "waiting_for_login"
    AUTHENTICATED = "authenticated"
    FAILED = "failed"
    EXPIRED = "expired"


class BrowserSessionRecord(BaseModel):
    """
    Internal representation of a temporary authenticated browser session.

    This model may contain Playwright authentication material and must never
    be returned directly through an API response.
    """

    browser_session_id: str = Field(
        min_length=1,
        description="Opaque identifier for the browser session.",
    )

    user_id: str = Field(
        min_length=1,
        description="Authenticated AgentVerse user who owns the session.",
    )

    tenant_id: str | None = Field(
        default=None,
        description="Optional tenant scope for the session.",
    )

    target_url: HttpUrl = Field(
        description="URL opened when the interactive browser session starts.",
    )

    target_domain: str = Field(
        min_length=1,
        description="Normalized domain associated with the target URL.",
    )

    status: BrowserSessionStatus = Field(
        default=BrowserSessionStatus.STARTING,
        description="Current browser-session lifecycle status.",
    )

    interactive_url: str | None = Field(
        default=None,
        description="Safe URL used by the client to access the interactive browser.",
    )

    success_url_contains: str | None = Field(
        default=None,
        description=(
            "Optional URL fragment used to detect successful authentication."
        ),
    )

    storage_state: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Playwright storage state containing cookies and local storage. "
            "Internal only."
        ),
    )

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
    )

    expires_at: datetime

    authenticated_at: datetime | None = None

    error: str | None = Field(
        default=None,
        description="Safe diagnostic message when the session fails.",
    )

    metadata: dict[str, Any] = Field(
        default_factory=dict,
    )

    @field_validator("created_at", "expires_at", "authenticated_at")
    @classmethod
    def ensure_timezone_aware(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is None:
            return None

        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)

        return value

    def is_expired(
        self,
        *,
        now: datetime | None = None,
    ) -> bool:
        current_time = now or datetime.now(UTC)
        return current_time >= self.expires_at

    def to_public_state(self) -> BrowserSessionPublicState:
        """
        Return the safe client-facing representation.

        Authentication material such as Playwright storage state is excluded.
        """

        return BrowserSessionPublicState(
            browser_session_id=self.browser_session_id,
            target_url=self.target_url,
            target_domain=self.target_domain,
            status=self.status,
            interactive_url=self.interactive_url,
            created_at=self.created_at,
            expires_at=self.expires_at,
            authenticated_at=self.authenticated_at,
            error=self.error,
        )


class BrowserSessionPublicState(BaseModel):
    """
    Safe browser-session state exposed to frontend and API consumers.
    """

    browser_session_id: str

    target_url: HttpUrl

    target_domain: str

    status: BrowserSessionStatus

    interactive_url: str | None = None

    created_at: datetime

    expires_at: datetime

    authenticated_at: datetime | None = None

    error: str | None = None