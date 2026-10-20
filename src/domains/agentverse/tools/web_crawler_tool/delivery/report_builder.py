from __future__ import annotations

from typing import Any

from src.domains.agentverse.tools.web_crawler_tool.delivery.reports import (
    build_report_delivery,
)


def build_report_refs(
    *,
    run_id: str,
    report_refs: list[Any],
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []

    for raw_ref in report_refs or []:
        if isinstance(raw_ref, str):
            report_id = raw_ref
            ref: dict[str, Any] = {
                "id": report_id,
            }

        elif isinstance(raw_ref, dict):
            ref = dict(raw_ref)

            report_id = str(
                ref.get("id")
                or ref.get("name")
                or "report"
            )

        else:
            continue

        ref["delivery"] = build_report_delivery(
            run_id=run_id,
            report_id=report_id,
            existing=ref.get("delivery"),
        )

        enriched.append(ref)

    return enriched