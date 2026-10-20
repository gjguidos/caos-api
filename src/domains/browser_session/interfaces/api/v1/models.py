# src/domains/browser_session/interfaces/api/v1/models.py

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

BrowserSessionStatusValue = Literal[
    "waiting_for_login",
    "authenticated",
    "failed",
    "expired",
]


class StartBrowserSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_url: HttpUrl
    tenant_id: str | None = None
    browser_session_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
    )
    ttl_seconds: int = Field(
        default=1800,
        ge=60,
        le=86_400,
    )

    # Retained for compatibility with the crawler UI.
    # The remote runner always owns the headed browser.
    headed: bool = True

    success_url_contains: str | None = None

class BrowserSessionCallbackPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    event: str = "browser_session.authenticated"
    browser_session_id: str | None = None
    runner_session_id: str | None = None
    domain: str | None = None
    target_url: str | None = None
    final_url: str | None = None
    storage_state: dict[str, Any] | None = None
    cookies_count: int | None = None
    matching_cookie_count: int | None = None
    origins_count: int | None = None
    secrets_redacted: bool = False


class BrowserSessionStatusRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    browser_session_id: str = Field(
        min_length=1,
        max_length=128,
    )
    tenant_id: str | None = None


class BrowserSessionResponse(BaseModel):
    """
    Safe public representation of a browser session.

    Never include storage_state, cookies, local storage, or tokens here.
    """

    model_config = ConfigDict(extra="ignore")

    ok: bool = True
    ready: bool

    browser_session_id: str
    status: BrowserSessionStatusValue

    domain: str
    target_url: str
    final_url: str | None = None
    interactive_url: str | None = None

    tenant_id: str | None = None
    ttl_seconds: int

    cookies_count: int | None = None
    matching_cookie_count: int | None = None
    origins_count: int | None = None

    secrets_redacted: bool = True

class BrowserSessionRenderRequest(BaseModel):
    url: str
    user_id: str | None = None
    wait_until: str = "domcontentloaded"
    wait_ms: int = Field(default=2500, ge=0, le=30000)
    max_html_bytes: int = Field(default=5_000_000, ge=1)

class BrowserSessionRenderResponse(BaseModel):
    success: bool = True
    browser_session_id: str
    url: str
    final_url: str
    status_code: int | None = None
    content_type: str | None = None
    title: str | None = None
    html: str
    html_size: int
    diagnostics: dict[str, Any] = {}
    secrets_redacted: bool = True

class BrowserSessionStatusResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ready: bool
    browser_session_id: str
    status: BrowserSessionStatusValue | None = None

    domain: str | None = None
    target_url: str | None = None
    final_url: str | None = None
    interactive_url: str | None = None

    user_id: str | None = None
    tenant_id: str | None = None
    ttl_seconds: int | None = None

    cookies_count: int | None = None
    matching_cookie_count: int | None = None
    origins_count: int | None = None

    secrets_redacted: bool = True


class DeleteBrowserSessionResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ok: bool = True
    deleted: bool
    browser_session_id: str
    user_id: str | None = None
    tenant_id: str | None = None
    secrets_redacted: bool = True