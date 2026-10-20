from __future__ import annotations

from typing import Any

from bs4 import BeautifulSoup

from src.domains.agentverse.tools.web_crawler_tool.analysis.protocol import (
    PageExtractor,
)
from src.domains.agentverse.tools.web_crawler_tool.helpers.html_report import (
    extract_media,
)
from src.domains.agentverse.tools.web_crawler_tool.reports.media_inventory import (
    build_page_media_inventory_report,
)


class MediaPageExtractor(PageExtractor):
    name = "media"

    def extract(
        self,
        *,
        html: str,
        url: str,
        final_url: str,
        raw_soup: BeautifulSoup,
    ) -> list[dict[str, Any]]:
        page_url = final_url or url

        media_report = build_page_media_inventory_report(
            html=html,
            page_url=page_url,
            html_report={
                "media": extract_media(
                    raw_soup,
                    base_url=page_url,
                )
            },
            soup=raw_soup,
        )

        return [
            item.model_dump(mode="json")
            for item in media_report.items
        ]