from __future__ import annotations

import json
import re
from typing import Any

from src.domains.agentverse.tools.web_crawler_tool.helpers.resources import (
    extension_from_url as _extension_from_url,
)
from src.domains.agentverse.tools.web_crawler_tool.helpers.resources import (
    filename_from_url_or_item as _filename_from_url_or_item,
)
from src.domains.agentverse.tools.web_crawler_tool.helpers.resources import (
    item_url as _item_url,
)

RESOURCE_REPORTS = {
    "media": "media",
    "documents": "document",
}

def find_in_crawled_pages(
    *,
    pages: list[dict[str, Any]],
    query: str | None,
    case_sensitive: bool = False,
    max_matches: int = 20,
    context_chars: int = 160,
    find_scope: str = "all",  # page | reports | all
    find_report: str | None = None,
) -> list[dict[str, Any]]:
    if not query or not query.strip():
        return []

    needle = query.strip()
    flags = 0 if case_sensitive else re.IGNORECASE
    pattern = re.compile(re.escape(needle), flags)

    matches: list[dict[str, Any]] = []

    for page in pages:
        page_url = page.get("final_url") or page.get("url") or ""
        page_title = page.get("title") or ""

        # ---------------------------------------------------------
        # 1. Normal page-level text search
        # ---------------------------------------------------------

        if find_scope in {"page", "all"}:
            page_fields = _build_page_search_fields(page)

            _append_text_matches(
                matches=matches,
                searchable_fields=page_fields,
                pattern=pattern,
                needle=needle,
                page_url=page_url,
                page_title=page_title,
                context_chars=context_chars,
                max_matches=max_matches,
            )

            if len(matches) >= max_matches:
                return matches

        # ---------------------------------------------------------
        # 2. Report search
        # ---------------------------------------------------------

        if find_scope in {"reports", "all"}:
            reports = page.get("reports") or {}

            report_keys = _ordered_report_keys(
                reports,
                find_report=find_report,
            )

            for report_key in report_keys:
                if not report_key:
                    continue

                report_value = reports.get(report_key)

                if report_value is None:
                    continue

                # Media/documents are inventories. Search their items
                # structurally instead of stringifying the whole report.
                if report_key in RESOURCE_REPORTS:
                    resource_type = RESOURCE_REPORTS[report_key]

                    resource_matches = _find_resource_matches(
                        report_value=report_value,
                        report_key=report_key,
                        resource_type=resource_type,
                        pattern=pattern,
                        needle=needle,
                        page_url=page_url,
                        page_title=page_title,
                    )

                    for match in resource_matches:
                        matches.append(match)

                        if len(matches) >= max_matches:
                            return matches

                    continue

                # Other reports keep the existing text-oriented behavior.
                searchable_fields = {
                    f"reports.{report_key}": _stringify_for_search(
                        report_value
                    )
                }

                _append_text_matches(
                    matches=matches,
                    searchable_fields=searchable_fields,
                    pattern=pattern,
                    needle=needle,
                    page_url=page_url,
                    page_title=page_title,
                    context_chars=context_chars,
                    max_matches=max_matches,
                )

                if len(matches) >= max_matches:
                    return matches

    return matches


def _append_text_matches(
    *,
    matches: list[dict[str, Any]],
    searchable_fields: dict[str, str],
    pattern: re.Pattern[str],
    needle: str,
    page_url: str,
    page_title: str,
    context_chars: int,
    max_matches: int,
) -> None:
    for field, value in searchable_fields.items():
        if not value:
            continue

        for hit in pattern.finditer(value):
            start, end = hit.span()

            before_start = max(0, start - context_chars)
            after_end = min(len(value), end + context_chars)

            matches.append(
                {
                    "match_kind": "text",
                    "page_url": page_url,
                    "page_title": page_title,
                    "field": field,
                    "query": needle,
                    "match_text": value[start:end].strip(),
                    "before": value[before_start:start].strip(),
                    "after": value[end:after_end].strip(),
                }
            )

            if len(matches) >= max_matches:
                return


def _find_resource_matches(
    *,
    report_value: Any,
    report_key: str,
    resource_type: str,
    pattern: re.Pattern[str],
    needle: str,
    page_url: str,
    page_title: str,
) -> list[dict[str, Any]]:
    items = _coerce_resource_items(
        report_value,
        report_key=report_key,
    )

    matches: list[dict[str, Any]] = []

    for item in items:
        resource_url = _item_url(item)
        filename = _filename_from_url_or_item(
            item=item,
            url=resource_url,
        )

        searchable = {
            "filename": filename,
            "url": resource_url,
            "title": str(item.get("title") or ""),
            "name": str(item.get("name") or ""),
            "alt": str(item.get("alt") or ""),
            "role": str(
                item.get("role")
                or item.get("media_role")
                or ""
            ),
            "type": str(
                item.get("type")
                or item.get("document_type")
                or ""
            ),
        }

        for field, value in searchable.items():
            if not value:
                continue

            hit = pattern.search(value)

            if not hit:
                continue

            matches.append(
                {
                    "match_kind": "resource",
                    "page_url": page_url,
                    "page_title": page_title,
                    "report": report_key,
                    "field": field,
                    "query": needle,
                    "match_text": hit.group(0),
                    "resource_type": resource_type,
                    "resource_url": resource_url,
                    "filename": filename,
                    "extension": _extension_from_url(resource_url),
                }
            )

            # One result per resource item.
            break

    return matches


def _build_page_search_fields(
    page: dict[str, Any],
) -> dict[str, str]:
    fields = {
        "title": page.get("title") or "",
        "text": page.get("text") or "",
        "links": "\n".join(
            link
            for link in (page.get("links") or [])
            if isinstance(link, str)
        ),
    }

    reports = page.get("reports") or {}

    html_components = (
        reports.get("html_components")
        or page.get("html_report")
        or {}
    )

    fields["headings"] = "\n".join(
        str(h.get("text") or "")
        for h in html_components.get("headings") or []
        if isinstance(h, dict)
    )

    return fields


def _coerce_resource_items(
    value: Any,
    *,
    report_key: str,
) -> list[dict[str, Any]]:
    if value is None:
        return []

    if isinstance(value, list):
        return [
            item
            for item in value
            if isinstance(item, dict)
        ]

    if isinstance(value, dict):
        candidate_keys = [
            "items",
            report_key,
            "results",
        ]

        if report_key == "media":
            candidate_keys.append("media")

        if report_key == "documents":
            candidate_keys.append("documents")

        for key in candidate_keys:
            nested = value.get(key)

            if isinstance(nested, list):
                return [
                    item
                    for item in nested
                    if isinstance(item, dict)
                ]

    return []


def _stringify_for_search(value: Any) -> str:
    if value is None:
        return ""

    if isinstance(value, str):
        return value

    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
    except TypeError:
        return str(value)
    
def _ordered_report_keys(
    reports: dict[str, Any],
    *,
    find_report: str | None,
) -> list[str]:
    if find_report:
        return [find_report] if find_report in reports else []

    priority = [
        "media",
        "documents",
    ]

    trailing = [
        "html_components",
    ]

    ordered: list[str] = []

    # Structured resource inventories first.
    for key in priority:
        if key in reports:
            ordered.append(key)

    # Normal reports next.
    for key in reports:
        if key not in priority and key not in trailing:
            ordered.append(key)

    # Large/noisy raw HTML representation last.
    for key in trailing:
        if key in reports:
            ordered.append(key)

    return ordered