from __future__ import annotations

from pydantic import BaseModel

from src.domains.agentverse.tools.web_crawler_tool.site_state.models import CrawlTarget


class CrawlChunk(BaseModel):
    index: int
    targets: list[CrawlTarget]

    @property
    def size(self) -> int:
        return len(self.targets)


class CrawlPlan(BaseModel):
    root_url: str
    strategy: str

    targets: list[CrawlTarget]
    chunks: list[CrawlChunk]

    discovered_count: int
    selected_count: int

    truncated: bool = False
    truncation_reason: str | None = None
    