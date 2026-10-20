from __future__ import annotations

from typing import Any

from src.domains.agentverse.tools.web_crawler_tool.filters import (
    apply_crawler_filters,
)
from src.domains.agentverse.tools.web_crawler_tool.helpers.resources import (
    infer_filter_from_find,
)
from src.domains.agentverse.tools.web_crawler_tool.planning.models import (
    CrawlPlan,
)
from src.domains.agentverse.tools.web_crawler_tool.runtime import (
    ResolvedCrawlerRuntime,
)
from src.domains.agentverse.tools.web_crawler_tool.search import (
    find_in_crawled_pages,
)
from src.domains.agentverse.tools.web_crawler_tool.site_state.models import (
    CoverageResult,
)


def project_crawl_result_worker(
    *,
    raw_result: dict[str, Any],
    runtime: ResolvedCrawlerRuntime,
    requested_reports: set[str] | None = None,
    coverage: CoverageResult | None = None,
    crawl_plan: CrawlPlan | None = None,
    asset_inventory: dict[str, Any] | None = None,
    crawl_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if requested_reports is None:
        requested_reports = normalize_requested_reports(
            runtime.report
        )

    return project_crawl_result(
        raw_result=raw_result,
        runtime=runtime,
        requested_reports=requested_reports,
        coverage=coverage,
        crawl_plan=crawl_plan,
        asset_inventory=asset_inventory,
        crawl_summary=crawl_summary,
    )


def project_crawl_result(
    *,
    raw_result: dict[str, Any],
    runtime: ResolvedCrawlerRuntime,
    requested_reports: set[str] | None = None,
    coverage: CoverageResult | None = None,
    crawl_plan: CrawlPlan | None = None,
    asset_inventory: dict[str, Any] | None = None,
    crawl_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Apply projection-only operations to an acquired crawl result.

    Acquisition has already completed before this function runs.
    This function does not perform network I/O.
    """

    find_scope = (
        runtime.params.find_scope
        or "all"
    )

    case_sensitive = bool(
        runtime.params.case_sensitive
    )

    max_matches = int(
        runtime.params.max_matches
        or 20
    )

    inferred_filters = infer_filter_from_find(
        runtime.params.find
    )

    media_extensions = merge_extensions(
        runtime.params.media_extensions,
        inferred_filters.get(
            "media_extensions"
        ),
    )

    document_extensions = merge_extensions(
        runtime.params.document_extensions,
        inferred_filters.get(
            "document_extensions"
        ),
    )

    # --------------------------------------------------------------
    # Search projection
    # --------------------------------------------------------------

    matches: list[dict[str, Any]] = []

    if (
        runtime.params.find
        and runtime.params.find.strip()
    ):
        matches = find_in_crawled_pages(
            pages=raw_result.get(
                "pages"
            ) or [],
            query=runtime.params.find,
            case_sensitive=case_sensitive,
            max_matches=max_matches,
            find_scope=find_scope,
            find_report=runtime.params.find_report,
        )

    raw_result["matches"] = matches
    raw_result["find"] = runtime.params.find
    raw_result["case_sensitive"] = case_sensitive
    raw_result["max_matches"] = max_matches
    raw_result["find_scope"] = find_scope
    raw_result["find_report"] = (
        runtime.params.find_report
    )

    # --------------------------------------------------------------
    # Filter projection
    # --------------------------------------------------------------

    has_filter_request = bool(
        media_extensions
        or runtime.params.media_filename
        or runtime.params.media_role
        or document_extensions
        or runtime.params.document_filename
        or runtime.params.document_type
    )

    if has_filter_request:
        raw_result = apply_crawler_filters(
            raw_result=raw_result,
            media_extensions=media_extensions,
            media_filename=runtime.params.media_filename,
            media_role=runtime.params.media_role,
            document_extensions=document_extensions,
            document_filename=runtime.params.document_filename,
            document_type=runtime.params.document_type,
        )
    else:
        raw_result.setdefault(
            "filter_summary",
            {
                "has_active_media_filter": False,
                "has_active_document_filter": False,
            },
        )

    filter_summary = (
        raw_result.get(
            "filter_summary"
        )
        or {}
    )

    has_active_filters = bool(
        filter_summary.get(
            "has_active_media_filter"
        )
        or filter_summary.get(
            "has_active_document_filter"
        )
    )

    # --------------------------------------------------------------
    # Report selection
    # --------------------------------------------------------------

    report_source: str | list[str] | None

    if requested_reports:
        report_source = sorted(
            requested_reports
        )
    else:
        report_source = runtime.report

    effective_reports = resolve_effective_reports(
        report=report_source,
        find=runtime.params.find,
        has_active_filters=has_active_filters,
    )

    raw_result["requested_reports"] = (
        sorted(requested_reports)
        if requested_reports
        else []
    )

    raw_result["profile_report"] = runtime.report
    raw_result["effective_reports"] = (
        effective_reports
    )
    raw_result["report"] = ",".join(
        effective_reports
    )

    # --------------------------------------------------------------
    # Runtime metadata
    # --------------------------------------------------------------

    raw_result["include_raw_pages"] = (
        runtime.include_raw_pages
    )

    raw_result["include_security_audit"] = (
        runtime.include_security_audit
    )

    raw_result["include_errors"] = (
        runtime.include_errors
    )

    raw_result["resolved_max_pages"] = (
        runtime.max_pages
    )

    raw_result["resolved_max_depth"] = (
        runtime.max_depth
    )

    raw_result["resolved_timeout_seconds"] = (
        runtime.timeout_seconds
    )

    raw_result["resolved_max_chars_per_page"] = (
        runtime.max_chars_per_page
    )

    raw_result["resolved_same_domain_only"] = (
        runtime.same_domain_only
    )

    raw_result["output_profile"] = (
        runtime.output_profile
    )

    raw_result["output_format"] = (
        runtime.output_format
    )

    raw_result["crawl_strategy"] = (
        runtime.crawl_strategy
    )

    # --------------------------------------------------------------
    # Evidence metadata
    # --------------------------------------------------------------

    raw_result["evidence"] = {
        "source": "parsed_page_store",
        "reused": False,
        "snapshot_id": None,
        "snapshot_created_at": None,
        "snapshot_age_seconds": None,
        "acquisition_fingerprint": None,
        "decision": (
            "site_state_coverage"
            if coverage is not None
            else "no_site_state"
        ),
    }

    # --------------------------------------------------------------
    # Coverage and planning metadata
    # --------------------------------------------------------------

    raw_result["coverage"] = (
        coverage.model_dump(
            mode="json"
        )
        if coverage is not None
        else None
    )

    raw_result["crawl_plan"] = (
        crawl_plan.model_dump(
            mode="json"
        )
        if crawl_plan is not None
        else None
    )
    raw_result["crawl_summary"] = crawl_summary or {}
    raw_result["asset_inventory"] = asset_inventory or {}

    return raw_result


def normalize_requested_reports(
    report: str | list[str] | None,
) -> set[str]:
    if report is None:
        return set()

    values = (
        report.split(",")
        if isinstance(report, str)
        else report
    )

    return {
        str(value).strip().lower()
        for value in values
        if str(value).strip()
    }


def resolve_effective_reports(
    *,
    report: str | list[str] | None,
    find: str | None,
    has_active_filters: bool,
) -> list[str]:
    if isinstance(report, str):
        reports = [
            item.strip()
            for item in report.split(",")
            if item.strip()
        ]
    elif isinstance(report, list):
        reports = [
            str(item).strip()
            for item in report
            if str(item).strip()
        ]
    else:
        reports = []

    if (
        find
        and find.strip()
        and "find" not in reports
    ):
        reports.append("find")

    if (
        has_active_filters
        and "filters" not in reports
    ):
        reports.append("filters")

    return list(
        dict.fromkeys(reports)
    )


def merge_extensions(
    explicit: list[str] | None,
    inferred: list[str] | None,
) -> list[str]:
    return list(
        dict.fromkeys(
            [
                *(explicit or []),
                *(inferred or []),
            ]
        )
    )