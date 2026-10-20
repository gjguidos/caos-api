from __future__ import annotations

from typing import Any

from src.domains.agentverse.tools.web_crawler_tool.reports.coverage import (
    build_coverage_metadata,
)
from src.domains.agentverse.tools.web_crawler_tool.types import (
    CrawlerFindMetadata,
    CrawlerFlagMetadata,
    CrawlerLimitMetadata,
    CrawlerMetadataReport,
    CrawlerTimingMetadata,
)


def build_metadata_report(
    *,
    start_url: str,
    mode: str,
    output_profile: str | None,
    output_format: str,
    report: str | list[str] | None,
    requested_reports: list[str],
    crawl_strategy: str,
    max_pages: int,
    max_depth: int,
    timeout_seconds: int,
    max_chars_per_page: int,
    same_domain_only: bool,
    include_links: bool,
    include_raw_pages: bool,
    include_security_audit: bool,
    include_errors: bool,
    started_at: str | None = None,
    finished_at: str | None = None,
    duration_ms: float | None = None,
    duration_seconds: float | None = None,
    filters: dict[str, Any] | None = None,
    find: str | None = None,
    case_sensitive: bool = False,
    max_matches: int = 20,
    find_scope: str = "all",
    find_report: str | None = None,
    coverage: dict[str, Any] | None = None,
    crawl_plan: dict[str, Any] | None = None,
) -> CrawlerMetadataReport:
    return CrawlerMetadataReport(
        start_url=start_url,
        coverage=build_coverage_metadata(
            coverage=coverage,
            crawl_plan=crawl_plan,
        ),
        mode=mode,
        output_profile=output_profile,
        output_format=output_format,
        report=report,
        requested_reports=sorted(requested_reports),
        crawl_strategy=crawl_strategy,
        timing=CrawlerTimingMetadata(
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            duration_seconds=duration_seconds,
            timezone="UTC",
        ),
        limits=CrawlerLimitMetadata(
            max_pages=max_pages,
            max_depth=max_depth,
            timeout_seconds=timeout_seconds,
            max_chars_per_page=max_chars_per_page,
        ),
        flags=CrawlerFlagMetadata(
            same_domain_only=same_domain_only,
            include_links=include_links,
            include_raw_pages=include_raw_pages,
            include_security_audit=include_security_audit,
            include_errors=include_errors,
        ),
        filters=filters or {},
        find=CrawlerFindMetadata(
            query=find,
            case_sensitive=case_sensitive,
            max_matches=max_matches,
            find_scope=find_scope,
            find_report=find_report,
        ),
    )

