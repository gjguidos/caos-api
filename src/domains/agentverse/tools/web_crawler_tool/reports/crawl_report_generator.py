from __future__ import annotations

from typing import Any

from src.domains.agentverse.tools.web_crawler_tool.reports.coverage import build_coverage_metadata
from src.domains.agentverse.tools.web_crawler_tool.reports.crawl_summary import (
    build_crawl_summary_report,
)
from src.domains.agentverse.tools.web_crawler_tool.reports.document_inventory import (
    build_site_document_inventory_report,
)
from src.domains.agentverse.tools.web_crawler_tool.reports.evidence import (
    build_evidence_metadata,
)
from src.domains.agentverse.tools.web_crawler_tool.reports.find import (
    build_find_report,
)
from src.domains.agentverse.tools.web_crawler_tool.reports.media_inventory import (
    build_site_media_inventory_report,
)
from src.domains.agentverse.tools.web_crawler_tool.reports.metadata import (
    build_metadata_report,
)
from src.domains.agentverse.tools.web_crawler_tool.reports.report_filters import (
    build_report_filters,
)
from src.domains.agentverse.tools.web_crawler_tool.reports.selection import (
    normalize_requested_reports,
)


def build_crawl_reports(
    *,
    pages: list[dict[str, Any]],
    start_url: str = "",
    mode: str = "static",
    output_profile: str | None = None,
    output_format: str = "markdown",
    crawl_strategy: str = "broad",
    max_pages: int = 0,
    max_depth: int = 0,
    timeout_seconds: int = 0,
    max_chars_per_page: int = 0,
    same_domain_only: bool = True,
    include_links: bool = True,
    include_raw_pages: bool = False,
    include_security_audit: bool = False,
    include_errors: bool = True,
    crawl_graph: dict[str, Any] | None = None,
    matches: list[dict[str, Any]] | None = None,
    errors: list[str] | None = None,
    security_audit: list[dict[str, Any]] | None = None,
    find: str | None = None,
    case_sensitive: bool = False,
    max_matches: int = 20,
    find_scope: str = "all",
    find_report: str | None = None,
    filters: dict[str, Any] | None = None,
    report: str | list[str] | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
    duration_ms: float | None = None,
    duration_seconds: float | None = None,
    evidence: dict[str, Any] | None = None,
    coverage: dict[str, Any] | None = None,
    crawl_plan: dict[str, Any] | None = None,
    asset_inventory: dict[str, Any] | None = None,
    asset_observations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    matches = matches or []
    errors = errors or []
    security_audit = security_audit or []
    filters = filters or {}
    asset_inventory = asset_inventory or {}

    requested_reports = normalize_requested_reports(report)

    reports: dict[str, Any] = {}

    if "metadata" in requested_reports:
        metadata_report = build_metadata_report(
            start_url=start_url,
            mode=mode,
            output_profile=output_profile,
            output_format=output_format,
            report=report,
            requested_reports=sorted(requested_reports),
            crawl_strategy=crawl_strategy,
            coverage=coverage,
            crawl_plan=crawl_plan,
            max_pages=max_pages,
            max_depth=max_depth,
            timeout_seconds=timeout_seconds,
            max_chars_per_page=max_chars_per_page,
            same_domain_only=same_domain_only,
            include_links=include_links,
            include_raw_pages=include_raw_pages,
            include_security_audit=include_security_audit,
            include_errors=include_errors,
            started_at=started_at,
            finished_at=finished_at,
            duration_ms=duration_ms,
            duration_seconds=duration_seconds,
            filters=filters,
            find=find,
            case_sensitive=case_sensitive,
            max_matches=max_matches,
            find_scope=find_scope,
            find_report=find_report,
        )

        reports["metadata"] = (
            metadata_report.model_dump()
        )

    if "crawl_graph" in requested_reports and crawl_graph:
        reports["crawl_graph"] = crawl_graph

    if "html_components" in requested_reports:
        reports["html_components"] = _collect_page_reports(
            pages=pages,
            report_key="html_components",
        )

    if "seo" in requested_reports:
        reports["seo"] = _collect_page_reports(
            pages=pages,
            report_key="seo",
        )

    if "links" in requested_reports:
        reports["links"] = _collect_page_reports(
            pages=pages,
            report_key="links",
            sum_keys=[
                "total_links",
                "unique_links",
                "internal_links",
                "external_links",
                "download_links",
                "social_links",
                "cta_candidates",
            ],
            unique_keys=["unique_domains"],
        )

    if "forms" in requested_reports:
        reports["forms"] = _collect_page_reports(
            pages=pages,
            report_key="forms",
            sum_keys=[
                "form_count",
                "total_controls",
                "required_controls",
                "unlabeled_controls",
                "sensitive_controls",
                "search_forms",
                "auth_forms",
                "newsletter_forms",
                "contact_forms",
                "external_action_forms",
            ],
        )

    if "content_blocks" in requested_reports:
        reports["content_blocks"] = _collect_page_reports(
            pages=pages,
            report_key="content_blocks",
            sum_keys=[
                "block_count",
                "total_text_len",
                "primary_evidence_blocks",
                "supporting_evidence_blocks",
                "text_heavy_blocks",
            ],
        )

    if "detected_html_blocks" in requested_reports:
        reports["detected_html_blocks"] = _collect_page_reports(
            pages=pages,
            report_key="detected_html_blocks",
            sum_keys=[
                "block_count",
                "hero_blocks",
                "navigation_blocks",
                "cta_blocks",
                "card_blocks",
                "media_blocks",
                "footer_blocks",
                "article_blocks",
            ],
        )

    if "snapshots" in requested_reports:
        reports["snapshots"] = _collect_page_reports(
            pages=pages,
            report_key="snapshot",
        )

    if "documents" in requested_reports:
        documents = build_site_document_inventory_report(
            asset_observations=asset_observations or [],
        )

        reports["documents"] = documents.model_dump()

    if "media" in requested_reports:
        media = build_site_media_inventory_report(
            pages=pages,
            asset_observations=asset_observations,
        )

        reports["media"] = media.model_dump()

    if "find" in requested_reports:
        reports["find"] = build_find_report(
            query=find,
            matches=matches,
            case_sensitive=case_sensitive,
            max_matches=max_matches,
            find_scope=find_scope,
            find_report=find_report,
        )

    if "filters" in requested_reports:
        reports["filters"] = build_report_filters(
            pages=pages,
            media_extensions=filters.get("media_extensions"),
            media_filename=filters.get("media_filename"),
            media_role=filters.get("media_role"),
            document_extensions=filters.get("document_extensions"),
            document_filename=filters.get("document_filename"),
            document_type=filters.get("document_type"),
        )
        
    coverage_metadata = build_coverage_metadata(
        coverage=coverage,
        crawl_plan=crawl_plan,
    )

    reports["crawl_summary"] = build_crawl_summary_report(
        pages=pages,
        reports=reports,
        matches=matches,
        errors=errors,
        security_audit=security_audit,
        coverage=coverage_metadata,
    )

    return reports


def _collect_page_reports(
    *,
    pages: list[dict[str, Any]],
    report_key: str,
    sum_keys: list[str] | None = None,
    unique_keys: list[str] | None = None,
) -> dict[str, Any]:
    page_reports: list[dict[str, Any]] = []

    for page in pages:
        page_reports_raw = page.get("reports") or {}
        report = page_reports_raw.get(report_key)

        if not report:
            continue

        page_reports.append(
            {
                "page_url": page.get("final_url") or page.get("url"),
                "page_title": page.get("title") or "",
                **report,
            }
        )

    payload: dict[str, Any] = {
        "page_count": len(page_reports),
        "page_reports": page_reports,
    }

    for key in sum_keys or []:
        payload[key] = sum(
            int(item.get(key) or 0)
            for item in page_reports
        )

    for key in unique_keys or []:
        values: set[Any] = set()

        for item in page_reports:
            raw = item.get(key)

            if isinstance(raw, list):
                values.update(value for value in raw if value is not None)
            elif raw is not None:
                values.add(raw)

        payload[key] = sorted(values)

    return payload
