from __future__ import annotations

from typing import Any


def build_report_delivery(
    *,
    run_id: str,
    report_id: str,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    delivery = dict(existing or {})

    url = delivery.get("url")

    if not url:
        if report_id in {"crawl_summary", "summary"}:
            url = (
                f"/api/v1/operations/{run_id}"
                "/reports/summary"
            )
        elif report_id == "metadata":
            url = (
                f"/api/v1/operations/{run_id}"
                "/reports/metadata"
            )
        elif report_id == "media":
            url = (
                f"/api/v1/operations/{run_id}"
                "/reports/media/stream"
            )
        elif report_id == "documents":
            url = (
                f"/api/v1/operations/{run_id}"
                "/reports/documents/stream"
            )
        else:
            url = (
                f"/api/v1/operations/{run_id}"
                f"/reports/{report_id}"
            )

    delivery.setdefault("url", url)
    delivery.setdefault("method", "GET")
    delivery.setdefault(
        "auth",
        {
            "type": "bearer",
        },
    )
    delivery.setdefault(
        "disposition",
        "attachment",
    )
    delivery.setdefault(
        "media_type",
        "application/json",
    )
    delivery.setdefault(
        "streaming",
        report_id in {"media", "documents"},
    )

    return delivery