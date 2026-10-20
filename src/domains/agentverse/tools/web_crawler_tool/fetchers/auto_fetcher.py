from __future__ import annotations

from typing import Any

from src.domains.agentverse.tools.web_crawler_tool.auth.context import CrawlAuthContext
from src.domains.agentverse.tools.web_crawler_tool.evaluation.fallback import (
    evaluate_browser_fallback,
)
from src.domains.agentverse.tools.web_crawler_tool.types import CrawledPage


class AutoFallbackFetcher:
    """
    Static-first fetcher with browser fallback.

    Browser fallback is only used when static extraction appears empty,
    weak, or JavaScript-dependent.
    """

    def __init__(
        self,
        *,
        static_fetcher: Any,
        browser_fetcher: Any | None,
        browser_enabled: bool,
        min_text_chars: int = 300,
        min_links: int = 1,
        min_headings: int = 1,
        score_threshold: int = 5,
    ):
        self.static_fetcher = static_fetcher
        self.browser_fetcher = browser_fetcher
        self.browser_enabled = browser_enabled
        self.min_text_chars = min_text_chars
        self.min_links = min_links
        self.min_headings = min_headings
        self.score_threshold = score_threshold

    async def fetch(
        self,
        url: str,
        *,
        auth_context: CrawlAuthContext | None = None,
        agent: Any | None = None,
    ) -> CrawledPage:
        static_page = await self.static_fetcher.fetch(url)

        fallback_report = evaluate_browser_fallback(
            static_page,
            min_text_chars=self.min_text_chars,
            min_links=self.min_links,
            min_headings=self.min_headings,
            score_threshold=self.score_threshold,
        )

        static_page.diagnostics = {
            **(static_page.diagnostics or {}),
            "quality_fallback_report": fallback_report.model_dump(),
            "auto_mode": True,
            "browser_fallback_considered": fallback_report.should_fallback,
            "browser_fallback_enabled": self.browser_enabled,
        }

        if not fallback_report.should_fallback:
            return static_page

        if not self.browser_enabled or self.browser_fetcher is None:
            static_page.diagnostics = {
                **(static_page.diagnostics or {}),
                "browser_fallback_used": False,
                "browser_fallback_skipped_reason": "browser_not_enabled",
            }
            return static_page

        browser_page = await self.browser_fetcher.fetch(
            url,
            auth_context=auth_context,
            agent=agent,
        )

        browser_page.diagnostics = {
            **(browser_page.diagnostics or {}),
            "quality_fallback_report": fallback_report.model_dump(),
            "auto_mode": True,
            "browser_fallback_used": True,
            "browser_fallback_reason": fallback_report.reasons,
            "browser_fallback_score": fallback_report.score,
            "static_snapshot": {
                "title": static_page.title,
                "text_len": len(static_page.text or ""),
                "link_count": len(static_page.links or []),
                "heading_count": len((static_page.html_report or {}).get("headings") or []),
                "error": static_page.error,
            },
        }

        return browser_page