from __future__ import annotations

from typing import Any

from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.tools.web_crawler_tool.config import (
    WebCrawlerSecurityConfig,
)
from src.domains.agentverse.tools.web_crawler_tool.evaluation.diagnostics import (
    build_static_page_diagnostics,
)
from src.domains.agentverse.tools.web_crawler_tool.helpers.parser import (
    parse_html,
)
from src.domains.agentverse.tools.web_crawler_tool.reports.page_report_generator import (
    build_page_reports,
)
from src.domains.agentverse.tools.web_crawler_tool.security.policy import (
    CrawlerSecurityError,
    enforce_url_security,
)
from src.domains.agentverse.tools.web_crawler_tool.types import CrawledPage


class BrowserPageFetcher:
    """
    Fetches pages through the browser_session ephemeral render endpoint.

    This fetcher does not own Playwright, Chromium, browser contexts, or
    request routing. The browser_session/browser_runner domains own that.
    """

    def __init__(
        self,
        *,
        timeout_seconds: int,
        user_agent: str,
        max_chars_per_page: int,
        allowed_content_types: list[str],
        max_response_bytes: int,
        security: WebCrawlerSecurityConfig,
        wait_until: str = "domcontentloaded",
        wait_ms: int = 2500,
        viewport_width: int = 1366,
        viewport_height: int = 768,
        block_resources: bool = False,
        allowed_resource_types: list[str] | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent
        self.max_chars_per_page = max_chars_per_page
        self.allowed_content_types = allowed_content_types
        self.max_response_bytes = max_response_bytes
        self.security = security

        self.wait_until = wait_until or "domcontentloaded"
        self.wait_ms = wait_ms

        # Kept for constructor compatibility. Actual browser runtime now lives
        # in browser_session/browser_runner.
        self.viewport_width = viewport_width
        self.viewport_height = viewport_height
        self.block_resources = block_resources
        self.allowed_resource_types = set(
            allowed_resource_types
            or ["document", "xhr", "fetch", "script", "stylesheet"]
        )

    async def fetch(
        self,
        url: str,
        *,
        auth_context: Any | None = None,
        context: dict[str, Any] | None = None,
        agent: Any | None = None,
    ) -> CrawledPage:
        try:
            enforce_url_security(
                url,
                security=self.security,
                start_url=url,
            )
        except CrawlerSecurityError as exc:
            return CrawledPage(
                url=url,
                final_url=url,
                title="",
                text=f"Blocked by security policy before browser render: {exc}",
                links=[],
                error="blocked_by_security_policy",
            )

        diagnostics: dict[str, Any] = {
            "renderer": "browser_session_ephemeral",
            "wait_until": self.wait_until,
            "wait_ms": self.wait_ms,
            "block_resources_requested": self.block_resources,
            "auth": (
                auth_context.safe_metadata()
                if auth_context and hasattr(auth_context, "safe_metadata")
                else {
                    "mode": "none",
                    "secrets_redacted": True,
                }
            ),
        }

        try:
            rest = self._require_agent_rest(agent)

            render_url = self._resolve_ephemeral_render_url(agent)

            rendered = await rest.fetch_json(
                url=render_url,
                method="POST",
                json_payload={
                    "url": url,
                    "wait_until": self.wait_until,
                    "wait_ms": self.wait_ms,
                    "max_html_bytes": self.max_response_bytes,
                },
            )

            rendered_page = self._unwrap_render_response(rendered)

            html = rendered_page.get("html") or ""
            final_url = rendered_page.get("final_url") or url
            status_code = rendered_page.get("status_code")
            content_type = (
                rendered_page.get("content_type")
                or "text/html"
            ).lower()
            title = rendered_page.get("title") or ""

            try:
                enforce_url_security(
                    final_url,
                    security=self.security,
                    start_url=url,
                )
            except CrawlerSecurityError as exc:
                return CrawledPage(
                    url=url,
                    final_url=final_url,
                    status_code=status_code,
                    content_type=content_type,
                    title="",
                    text=f"Blocked final browser URL: {exc}",
                    links=[],
                    diagnostics=diagnostics,
                    error="blocked_final_url",
                )

            if content_type and not any(
                allowed in content_type
                for allowed in self.allowed_content_types
            ):
                return CrawledPage(
                    url=url,
                    final_url=final_url,
                    status_code=status_code,
                    content_type=content_type,
                    title="",
                    text=f"Skipped unsupported content type: {content_type}",
                    links=[],
                    diagnostics=diagnostics,
                    error="unsupported_content_type",
                )

            html_size = len(
                html.encode(
                    "utf-8",
                    errors="ignore",
                )
            )

            if html_size > self.max_response_bytes:
                return CrawledPage(
                    url=url,
                    final_url=final_url,
                    status_code=status_code,
                    content_type=content_type,
                    title="",
                    text="Skipped rendered HTML larger than max_response_bytes.",
                    links=[],
                    diagnostics={
                        **diagnostics,
                        "html_size": html_size,
                    },
                    error="response_too_large",
                )

            parsed_title, text, links = parse_html(
                html=html,
                base_url=final_url,
                max_chars=self.max_chars_per_page,
            )

            title = title or parsed_title

            if not title and not text.strip() and not links:
                text = (
                    f"Browser rendered empty or near-empty DOM. "
                    f"final_url={final_url}, status={status_code}, "
                    f"content_type={content_type}, html_size={html_size}"
                )

            diagnostics = {
                **diagnostics,
                **build_static_page_diagnostics(html),
                **(rendered_page.get("diagnostics") or {}),
                "fetcher": "browser_ephemeral",
                "renderer": "browser_session_ephemeral",
                "ephemeral": True,
                "authenticated_session_reused": False,
                "secrets_redacted": True,
                "html_size": html_size,
                "final_url": final_url,
                "status_code": status_code,
                "content_type": content_type,
                "link_count": len(links or []),
            }

            AGENTVERSE_LOGGER.warning(
                LogDomain.TOOL_BAY,
                "[🕷️ WEB CRAWLER][BROWSER EPHEMERAL FETCHER][PARSED PAGE DEBUG]",
                extra={
                    "url": url,
                    "final_url": final_url,
                    "title": title,
                    "status_code": status_code,
                    "content_type": content_type,
                    "text_len": len(text or ""),
                    "link_count": len(links or []),
                    "html_size": html_size,
                    "sample_links": (links or [])[:5],
                },
            )

            html_components, reports = build_page_reports(
                html=html,
                url=url,
                final_url=final_url,
                title=title,
                text=text,
                links=links,
                status_code=status_code,
                content_type=content_type,
                diagnostics=diagnostics,
                max_text_chars_per_block=1200,
                min_text_len=80,
            )

            return CrawledPage(
                url=url,
                final_url=final_url,
                status_code=status_code,
                content_type=content_type,
                title=title,
                text=text,
                links=links,
                raw_html=html,
                html_report=html_components,
                diagnostics=diagnostics,
                reports=reports,
            )

        except Exception as exc:
            error_message = f"{type(exc).__name__}: {exc}"

            return CrawledPage(
                url=url,
                final_url=url,
                title="",
                text=f"Browser ephemeral fetch failed: {error_message}",
                links=[],
                diagnostics=diagnostics,
                error=error_message,
            )

    def _require_agent_rest(
        self,
        agent: Any | None,
    ) -> Any:
        if agent is None:
            raise RuntimeError(
                "REST capability is required, but no agent runtime is bound to this tool."
            )

        rest = getattr(agent, "rest", None)

        if rest is None or not callable(getattr(rest, "fetch_json", None)):
            raise RuntimeError(
                "REST capability is required, but this agent does not expose rest.fetch_json(...)."
            )

        return rest

    def _resolve_ephemeral_render_url(
        self,
        agent: Any,
    ) -> str:
        public_settings = getattr(agent, "public_settings", {}) or {}
        browser_session_settings = (
            public_settings.get("browser_session") or {}
        )

        render_url = browser_session_settings.get(
            "browser_session_ephemeral_render_url"
        )

        if render_url:
            return str(render_url)

        raise RuntimeError(
            "Browser session ephemeral render URL is not available in public settings."
        )

    @staticmethod
    def _unwrap_render_response(
        rendered: Any,
    ) -> dict[str, Any]:
        if not isinstance(rendered, dict):
            raise RuntimeError(
                "Browser session render response must be a JSON object."
            )

        response_payload = rendered.get("data")

        if isinstance(response_payload, dict):
            return response_payload

        return rendered