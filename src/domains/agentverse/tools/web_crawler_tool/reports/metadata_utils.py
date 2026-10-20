from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo


DEFAULT_REPORT_TIMEZONE = "Europe/Stockholm"


def build_report_timestamp(
    *,
    timezone: str = DEFAULT_REPORT_TIMEZONE,
) -> dict[str, str]:
    now = datetime.now(ZoneInfo(timezone))

    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "generated_date": now.date().isoformat(),
        "timezone": timezone,
    }


def build_report_source_metadata(
    *,
    url: str,
    final_url: str | None = None,
    status_code: int | None = None,
    content_type: str | None = None,
    title: str = "",
) -> dict[str, Any]:
    return {
        "url": url,
        "final_url": final_url or url,
        "status_code": status_code,
        "content_type": content_type,
        "title": title,
    }


def build_page_report_summary(
    *,
    url: str,
    final_url: str | None,
    status_code: int | None,
    content_type: str | None,
    title: str,
    reports: dict[str, Any],
    timezone: str = DEFAULT_REPORT_TIMEZONE,
) -> dict[str, Any]:
    generated_reports = sorted(reports.keys())

    return {
        "contract_version": "crawler.page_reports.v1",
        **build_report_timestamp(timezone=timezone),
        "generated_reports": generated_reports,
        "report_count": len(generated_reports),
        "source": build_report_source_metadata(
            url=url,
            final_url=final_url,
            status_code=status_code,
            content_type=content_type,
            title=title,
        ),
        "summary": {
            "seo_grade": _get_nested(reports, "seo", "grade", default="unknown"),
            "content_block_count": _get_nested(
                reports, "content_blocks", "block_count", default=0
            ),
            "detected_html_block_count": _get_nested(
                reports, "detected_html_blocks", "block_count", default=0
            ),
            "unique_links": _get_nested(reports, "links", "unique_links", default=0),
            "total_media": _get_nested(reports, "media", "total_media", default=0),
            "document_count": _get_nested(
                reports, "documents", "document_count", default=0
            ),
            "form_count": _get_nested(reports, "forms", "form_count", default=0),
        },
    }


def _get_nested(
    value: dict[str, Any],
    *keys: str,
    default: Any = None,
) -> Any:
    current: Any = value

    for key in keys:
        if not isinstance(current, dict):
            return default

        current = current.get(key)

        if current is None:
            return default

    return current