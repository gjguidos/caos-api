from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from src.domains.agentverse.tools.web_crawler_tool.types import (
    CrawledPage,
    CrawlGraphReport,
    CrawlResult,
    SecurityAuditEntry,
)


class PageSnapshot(BaseModel):
    url: str
    final_url: str | None = None
    title: str = ""
    content_hash: str = ""
    structure_hash: str = ""
    link_hash: str = ""
    metadata_hash: str = ""
    form_hash: str = ""
    component_hash: str = ""
    snapshot_version: str = "1.0"
    metadata: dict[str, Any] = Field(default_factory=dict)


class SnapshotChangeSummary(BaseModel):
    content_changed: bool = False
    structure_changed: bool = False
    links_changed: bool = False
    metadata_changed: bool = False
    forms_changed: bool = False
    components_changed: bool = False


class SnapshotReport(BaseModel):
    contract_version: str = "crawler.snapshot.v1"

    current: PageSnapshot
    previous: PageSnapshot | None = None
    changed: bool = False
    changed_fields: list[str] = Field(default_factory=list)
    change_summary: SnapshotChangeSummary = Field(default_factory=SnapshotChangeSummary)

class CrawlSnapshot(BaseModel):
    schema_version: str = "crawler.snapshot.v1"

    snapshot_id: str = Field(
        default_factory=lambda: f"crawl_{uuid4().hex}"
    )

    session_id: str
    user_id: str | None = None

    start_url: str
    domain: str
    mode: str

    acquisition_fingerprint: str

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC)
    )

    acquisition: dict[str, Any] = Field(
        default_factory=dict
    )

    pages: list[dict[str, Any]] = Field(
        default_factory=list
    )

    errors: list[str] = Field(
        default_factory=list
    )

    security_audit: list[dict[str, Any]] = Field(
        default_factory=list
    )

    crawl_graph: CrawlGraphReport

    @classmethod
    def from_crawl_result(
        cls,
        *,
        result: CrawlResult,
        session_id: str,
        user_id: str | None,
        domain: str,
        acquisition_fingerprint: str,
        acquisition: dict[str, Any],
    ) -> CrawlSnapshot:
        return cls(
            session_id=session_id,
            user_id=user_id,
            start_url=result.start_url,
            domain=domain,
            mode=result.mode,
            acquisition_fingerprint=acquisition_fingerprint,
            acquisition=acquisition,
            pages=[
                page.model_dump(mode="json")
                for page in result.pages
            ],
            errors=list(result.errors),
            security_audit=[
                entry.model_dump(mode="json")
                for entry in result.security_audit
            ],
            crawl_graph=result.crawl_graph,
        )

    def to_crawl_result(self) -> CrawlResult:
        pages = [
            CrawledPage.model_validate(page)
            for page in self.pages
        ]

        security_audit = [
            SecurityAuditEntry.model_validate(entry)
            for entry in self.security_audit
        ]

        return CrawlResult(
            start_url=self.start_url,
            mode=self.mode,
            page_count=len(pages),
            pages=pages,
            errors=list(self.errors),
            security_audit=security_audit,
            crawl_graph=self.crawl_graph,
        )