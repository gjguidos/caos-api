from __future__ import annotations

from typing import Any

from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.tools.web_crawler_tool.evaluation.diagnostics import (
    build_static_page_diagnostics,
)
from src.domains.agentverse.tools.web_crawler_tool.helpers.parser import parse_html
from src.domains.agentverse.tools.web_crawler_tool.reports.page_report_generator import (
    build_page_reports,
)
from src.domains.agentverse.tools.web_crawler_tool.types import CrawledPage


class BrowserSessionPageFetcher:
    """
    Fetches pages through an authenticated browser session owned by
    the browser_session/browser_runner domains.

    This fetcher does not use Playwright directly and does not know about
    cookies, localStorage, or storage_state.
    """

    def __init__(
        self,
        *,
        timeout_seconds: int,
        max_chars_per_page: int,
        max_response_bytes: int,
        wait_until: str = "domcontentloaded",
        wait_ms: int = 2500,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_chars_per_page = max_chars_per_page
        self.max_response_bytes = max_response_bytes
        self.wait_until = wait_until
        self.wait_ms = wait_ms

    async def fetch(
        self,
        url: str,
        *,
        auth_context: Any | None = None,
        agent: Any | None = None,
        context: dict[str, Any] | None = None,
    ) -> CrawledPage:
        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            f"[🕷️ WEB CRAWLER][BROWSER_SESSION_FETCHER][START_FETCHER] {url}:{auth_context}",
        )
        if auth_context is None:
            raise RuntimeError(
                "Authenticated browser-session fetch requires auth_context."
            )

        browser_session_id = getattr(
            auth_context,
            "browser_session_id",
            None,
        )

        if not browser_session_id:
            raise RuntimeError(
                "Authenticated browser-session fetch requires browser_session_id."
            )

        user_id = (
            getattr(agent, "user_id", None)
            or getattr(auth_context, "user_id", None)
        )

        if not user_id:
            raise RuntimeError(
                "Authenticated browser-session fetch requires user_id."
            )

        rest = getattr(agent, "rest", None)

        if rest is None:
            raise RuntimeError(
                "REST capability is not available on this agent."
            )

        public_settings = getattr(agent, "public_settings", {}) or {}
        browser_session_settings = public_settings.get("browser_session") or {}

        render_url_template = browser_session_settings.get(
            "browser_session_render_page_url_template"
        )

        if not render_url_template:
            raise RuntimeError(
                "Browser session render URL template is not available in public settings."
            )

        render_url = render_url_template.format(
            browser_session_id=str(browser_session_id)
        )

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            f"[🕷️ WEB CRAWLER][BROWSER_SESSION_FETCHER][START FETCHING] {render_url}:{user_id}",
        )

        rendered = await rest.fetch_json(
            url=render_url,
            method="POST",
            json_payload={
                "url": url,
                "user_id": user_id,
                "wait_until": self.wait_until,
                "wait_ms": self.wait_ms,
                "max_html_bytes": self.max_response_bytes,
            },
        )

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            f"[🕷️ WEB CRAWLER][BROWSER_SESSION_FETCHER][START FETCHED] {render_url}:{user_id}",
        )

        rendered_keys = list(rendered.keys()) if isinstance(rendered, dict) else []
        AGENTVERSE_LOGGER.warning(
            LogDomain.TOOL_BAY,
            f"[🕷️ WEB CRAWLER][BROWSER SESSION FETCHER][RENDER RESPONSE DEBUG] rendered keys: {rendered_keys}",
            

        )

        response_payload = rendered.get("data") if isinstance(rendered, dict) else None

        if isinstance(response_payload, dict):
            rendered_page = response_payload
        else:
            rendered_page = rendered

        html = rendered_page.get("html") or ""
        final_url = rendered_page.get("final_url") or url
        status_code = rendered_page.get("status_code")
        content_type = rendered_page.get("content_type") or "text/html"
        title = rendered_page.get("title") or ""

        AGENTVERSE_LOGGER.warning(
            LogDomain.TOOL_BAY,
            "[🕷️ WEB CRAWLER][BROWSER SESSION FETCHER][RENDERED PAGE DEBUG]",
            extra={
                "url": url,
                "final_url": final_url,
                "status_code": status_code,
                "content_type": content_type,
                "title": title,
                "html_size": len(html.encode("utf-8", errors="ignore")),
            },
        )

        parsed_title, parsed_text, parsed_links = parse_html(
            html=html,
            base_url=final_url,
            max_chars=self.max_chars_per_page,
        )

        diagnostics = {
            **build_static_page_diagnostics(html),
            **(rendered_page.get("diagnostics") or {}),
            "fetcher": "browser_session",
            "authenticated_session_reused": True,
            "secrets_redacted": True,
            "html_size": len(html.encode("utf-8", errors="ignore")),
            "final_url": final_url,
            "status_code": status_code,
            "content_type": content_type,
            "link_count": len(parsed_links or []),
        }

        AGENTVERSE_LOGGER.warning(
            LogDomain.TOOL_BAY,
            "[🕷️ WEB CRAWLER][BROWSER SESSION FETCHER][PARSED PAGE DEBUG]",
            extra={
                "url": url,
                "final_url": final_url,
                "title": title or parsed_title,
                "text_len": len(parsed_text or ""),
                "link_count": len(parsed_links or []),
                "sample_links": (parsed_links or [])[:5],
            },
        )

        html_components, reports = build_page_reports(
            html=html,
            url=url,
            final_url=final_url,
            title=title or parsed_title,
            text=parsed_text,
            links=parsed_links,
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
            title=title or parsed_title,
            text=parsed_text,
            links=parsed_links,
            raw_html=html,
            html_report=html_components,
            diagnostics=diagnostics,
            reports=reports,
        )