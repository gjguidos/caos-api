from __future__ import annotations

from typing import Any

from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.tools.web_crawler_tool.markdown_builder import (
    build_crawler_markdown,
)
from src.domains.agentverse.tools.web_crawler_tool.reports.crawl_report_generator import (
    build_crawl_reports,
)
from src.domains.agentverse.tools.web_crawler_tool.types import (
    CrawlerMeta,
    CrawlerResultAddons,
    CrawlerToolResult,
)


def format_crawler_result(
    data: dict[str, Any],
    *,
    output_format: str = "markdown",
    asset_observations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    result = data.get("result") or data
    evidence = result.get("evidence") or {}
    start_url = result.get("start_url", "")
    mode = result.get("mode", "static")
    report = result.get("report")

    output_format = (output_format or "markdown").strip().lower()

    include_raw_pages = bool(result.get("include_raw_pages", False))
    include_security_audit = bool(result.get("include_security_audit", False))
    include_errors = bool(result.get("include_errors", True))

    pages = list(result.get("pages") or [])
    matches = list(result.get("matches") or [])
    find = result.get("find")

    errors = list(result.get("errors") or [])
    security_audit = list(result.get("security_audit") or [])
    crawl_graph = result.get("crawl_graph")

    asset_inventory = result.get("asset_inventory") or {}

    print(
        "ASSET_INVENTORY_TRACE",
        type(asset_inventory).__name__,
        asset_inventory.keys()
        if isinstance(asset_inventory, dict)
        else len(asset_inventory),
    )

    AGENTVERSE_LOGGER.info(
        LogDomain.TOOL_BAY,
        (
            "[DOCUMENT DEBUG][FORMATTER] "
            f"asset_observations={len(asset_observations or [])}"
        ),
    )


    reports = build_crawl_reports(
        pages=pages,
        asset_inventory=asset_inventory,
        start_url=start_url,
        mode=mode,
        output_profile=result.get("output_profile"),
        output_format=output_format,
        coverage=result.get("coverage"),
        crawl_plan=result.get("crawl_plan"),
        crawl_strategy=result.get("crawl_strategy") or "broad",
        max_pages=int(result.get("resolved_max_pages") or result.get("max_pages") or 0),
        max_depth=int(result.get("resolved_max_depth") or result.get("max_depth") or 0),
        timeout_seconds=int(
            result.get("resolved_timeout_seconds")
            or result.get("timeout_seconds")
            or 0
        ),
        max_chars_per_page=int(
            result.get("resolved_max_chars_per_page")
            or result.get("max_chars_per_page")
            or 0
        ),
        same_domain_only=bool(result.get("resolved_same_domain_only", True)),
        include_links=bool(result.get("include_links", True)),
        include_raw_pages=include_raw_pages,
        include_security_audit=include_security_audit,
        include_errors=include_errors,
        crawl_graph=crawl_graph,
        matches=matches,
        errors=errors,
        security_audit=security_audit,
        find=find,
        case_sensitive=bool(result.get("case_sensitive", False)),
        max_matches=int(result.get("max_matches") or 20),
        find_scope=result.get("find_scope") or "all",
        find_report=result.get("find_report"),
        filters=result.get("filters") or {},
        report=result.get("report"),
        started_at=result.get("started_at"),
        finished_at=result.get("finished_at"),
        duration_ms=result.get("duration_ms"),
        duration_seconds=result.get("duration_seconds"),
        evidence=evidence,
        asset_observations=asset_observations,
    )

    if output_format == "json":
        answer = "Crawl completed. Structured data is available in result.reports."
    else:
        answer = build_crawler_markdown(
            start_url=start_url,
            mode=mode,
            pages=pages,
            errors=errors if include_errors else [],
            report=report,
            find=find,
            matches=matches,
            security_audit=security_audit if include_security_audit else [],
            reports=reports,
        )

    components = (
        reports.get("html_components", {}).get("page_reports", [])
        if isinstance(reports.get("html_components"), dict)
        else []
    )

    crawl_summary = result.get("crawl_summary") or {}

    reported_page_count = int(
        crawl_summary.get("acquired")
        or len(pages)
    )

    contract = CrawlerToolResult(
        answer=answer,
        pages=pages if include_raw_pages else [],
        components=components,
        matches=matches,
        reports=reports,
        addons=CrawlerResultAddons(
            crawler_meta=CrawlerMeta(
                start_url=start_url,
                mode=mode,
                page_count=reported_page_count,
                report=report,
                find=find,
                match_count=len(matches),
                error_count=len(errors),
            ),
            errors=errors if include_errors else [],
            security_audit=security_audit if include_security_audit else [],
        ),
    )

    return {"result": contract.model_dump()}