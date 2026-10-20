# execution/scope.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.domains.agentverse.tools.web_crawler_tool.planning.models import (
    CrawlPlan,
)


@dataclass
class CrawlScopeResult:
    crawl_plan: CrawlPlan | None
    coverage: Any | None = None
    sitemap_plan: Any | None = None
    metadata: dict[str, Any] | None = None

    @property
    def has_targets(self) -> bool:
        return bool(
            self.crawl_plan
            and self.crawl_plan.targets
        )