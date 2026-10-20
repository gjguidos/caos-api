from __future__ import annotations

from pydantic import BaseModel


class SitemapCrawlChunkPayload(BaseModel):
    chunk_index: int
    url_hashes: list[str]
