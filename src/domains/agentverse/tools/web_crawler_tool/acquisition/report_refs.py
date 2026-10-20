from __future__ import annotations

from typing import Any

from src.domains.agentverse.tools.web_crawler_tool.runtime import (
    ResolvedCrawlerRuntime,
)


def build_report_refs(
    *,
    run_id: str,
    runtime: ResolvedCrawlerRuntime,
) -> dict[str, dict[str, Any]]:
    requested = runtime.report
    if requested is None:
        report_names: set[str] = set()
    elif isinstance(requested, str):
        report_names = {
            item.strip().lower()
            for item in requested.split(",")
            if item.strip()
        }
    else:
        report_names = {
            str(item).strip().lower()
            for item in requested
            if str(item).strip()
        }
    if "crawl_summary" in report_names:
        report_names.remove("crawl_summary")
        report_names.add("summary")

    report_names.add("summary")
    report_names.add("metadata")
    return {
        report_name: {
            "run_id": run_id,
            "report_type": report_name,
            "query": {
                "run_id": run_id,
            },
        }
        for report_name in sorted(report_names)
    }