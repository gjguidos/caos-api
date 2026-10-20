from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from pydantic import SecretStr

from src.domains.agentverse.tools.web_crawler_tool.auth.context import (
    CrawlAuthContext,
)


class CrawlAuthContextResolver:
    async def resolve(
        self,
        *,
        auth_mode: str,
        target_url: str,
        context: dict | None = None,
        browser_session_id: str | None = None,
        agent: Any | None = None,
    ) -> CrawlAuthContext:
        ctx = context or {}
        resolved_auth_mode = (auth_mode or "none").strip().lower()

        parsed = urlparse(target_url)
        domain = parsed.netloc.lower()

        user_id = (
            ctx.get("user_id")
            or getattr(agent, "user_id", None)
        )

        if resolved_auth_mode in {"", "none"}:
            return CrawlAuthContext(
                mode="none",
                provider="none",
                subject_user_id=str(user_id) if user_id else None,
                allowed_domains=[domain],
            )

        if resolved_auth_mode in {"delegated_session", "browser_context"}:
            resolved_browser_session_id = (
                browser_session_id
                or ctx.get("browser_session_id")
            )

            if not resolved_browser_session_id:
                raise ValueError(
                    "auth_mode=delegated_session requires browser_session_id."
                )

            return CrawlAuthContext(
                mode="browser_context",
                provider="browser_session_domain",
                subject_user_id=str(user_id) if user_id else None,
                browser_session_id=str(resolved_browser_session_id),
                allowed_domains=[domain],

                # Optional compatibility only:
                storage_state_ref=SecretStr(
                    f"browser-session://{resolved_browser_session_id}"
                ),
            )

        if resolved_auth_mode == "delegated_entra":
            raise NotImplementedError(
                "delegated_entra bearer-token crawling is not implemented yet."
            )

        raise ValueError(f"Unsupported crawler auth_mode: {resolved_auth_mode}")