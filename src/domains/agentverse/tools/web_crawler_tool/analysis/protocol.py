from __future__ import annotations

from typing import Any, Protocol

from bs4 import BeautifulSoup

from src.domains.agentverse.tools.web_crawler_tool.analysis.models import (
    PageAnalysis,
)
from src.domains.agentverse.tools.web_crawler_tool.contributions.models import (
    ReportContributionPayload,
)
from src.domains.agentverse.tools.web_crawler_tool.execution.models import (
    ProcessedPagePayload,
    ProcessedPageRecord,
)


class PageExtractor(Protocol):
    name: str

    def extract(
        self,
        *,
        html: str,
        url: str,
        final_url: str,
        raw_soup: BeautifulSoup,
    ) -> list[dict[str, Any]]:
        ...

class AnalysisContributionBuilder(Protocol):
    def __call__(
        self,
        *,
        payload: ProcessedPagePayload,
        record: ProcessedPageRecord,
        analysis: PageAnalysis,
    ) -> ReportContributionPayload:
        ...