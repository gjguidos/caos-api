from __future__ import annotations

from typing import Any

from bs4 import BeautifulSoup

from src.domains.agentverse.tools.web_crawler_tool.analysis.models import (
    PageAnalysis,
)
from src.domains.agentverse.tools.web_crawler_tool.analysis.protocol import (
    PageExtractor,
)


class PageAnalysisParser:
    def __init__(
        self,
        *,
        requested_reports: set[str] | list[str] | None = None,
        media_extractor: PageExtractor | None = None,
        document_extractor: PageExtractor | None = None,
    ) -> None:
        self.requested_reports = (
            None
            if requested_reports is None
            else {
                str(value).strip().lower()
                for value in requested_reports
                if str(value).strip()
            }
        )

        self.media_extractor = media_extractor
        self.document_extractor = document_extractor

    def wants(self, name: str) -> bool:
        return (
            self.requested_reports is None
            or "all" in self.requested_reports
            or name in self.requested_reports
        )

    def parse(
        self,
        *,
        url: str,
        final_url: str,
        status_code: int,
        content_type: str,
        html: str,
    ) -> PageAnalysis:
        html = html or ""

        raw_soup = BeautifulSoup(
            html,
            "lxml",
        )

        media_items: list[dict[str, Any]] = []
        document_items: list[dict[str, Any]] = []

        if (
            (
                self.wants("media")
                or self.wants("asset_light")
            )
            and self.media_extractor
        ):
            media_items = self.media_extractor.extract(
                html=html,
                url=url,
                final_url=final_url,
                raw_soup=raw_soup,
            )

        if self.wants("documents") and self.document_extractor:
            document_items = self.document_extractor.extract(
                html=html,
                url=url,
                final_url=final_url,
                raw_soup=raw_soup,
            )

        return PageAnalysis(
            url=url,
            final_url=final_url,
            status_code=status_code,
            content_type=content_type,
            media_items=media_items,
            document_items=document_items,
        )