from __future__ import annotations

from copy import deepcopy
from typing import Any

from bs4 import BeautifulSoup

from src.domains.agentverse.tools.web_crawler_tool.evaluation.diagnostics import (
    build_static_page_diagnostics,
)
from src.domains.agentverse.tools.web_crawler_tool.helpers.parser import (
    parse_html,
)
from src.domains.agentverse.tools.web_crawler_tool.reports.page_report_generator import (
    build_page_reports,
)
from src.domains.agentverse.tools.web_crawler_tool.types import (
    CrawledPage,
)


class PageParser:
    def __init__(
        self,
        *,
        max_chars_per_page: int,
        requested_reports: set[str] | list[str] | None = None,
        include_diagnostics: bool = False,
    ) -> None:
        self.max_chars_per_page = max_chars_per_page
        self.include_diagnostics = include_diagnostics

        self.requested_reports = (
            {
                str(report).strip().lower()
                for report in requested_reports
                if str(report).strip()
            }
            if requested_reports is not None
            else None
        )

    def _wants(self, report: str) -> bool:
        return (
            self.requested_reports is None
            or "all" in self.requested_reports
            or report in self.requested_reports
        )

    def parse(
        self,
        *,
        url: str,
        final_url: str,
        status_code: int,
        content_type: str,
        html: str,
    ) -> CrawledPage:
        html = html or ""

        # Parse the document only once.
        raw_soup = BeautifulSoup(html, "lxml")

        needs_parse_html = any(
            self._wants(report)
            for report in (
                "links",
                "documents",
                "content_blocks",
                "detected_html_blocks",
                "find",
            )
        )

        if needs_parse_html:
            # Clone the already-parsed tree instead of parsing HTML again.
            clean_soup = deepcopy(raw_soup)

            for element in clean_soup(
                ["script", "style", "noscript", "svg"]
            ):
                element.decompose()

            title, text, links = parse_html(
                html=html,
                base_url=final_url,
                max_chars=self.max_chars_per_page,
                clean_soup=clean_soup,
            )
        else:
            clean_soup = None

            title_element = raw_soup.title
            title = (
                title_element.get_text(" ", strip=True)
                if title_element is not None
                else ""
            )
            text = ""
            links = []

        diagnostics = (
            build_static_page_diagnostics(
                html,
                soup=raw_soup,
            )
            if self.include_diagnostics
            else {}
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
            raw_soup=raw_soup,
            clean_soup=clean_soup,
            requested_reports=self.requested_reports,
        )

        return CrawledPage(
            url=url,
            final_url=final_url,
            status_code=status_code,
            content_type=content_type,
            title=title,
            text=text,
            links=links,
            html_report=html_components,
            diagnostics=diagnostics,
            reports=reports,
        )