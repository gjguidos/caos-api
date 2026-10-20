from __future__ import annotations

from typing import Any


def build_find_report(
    *,
    query: str | None,
    matches: list[dict[str, Any]],
    case_sensitive: bool = False,
    max_matches: int = 20,
    find_scope: str = "all",
    find_report: str | None = None,
) -> dict[str, Any]:
    matches = matches or []

    matched_fields = sorted(
        {
            str(match.get("field"))
            for match in matches
            if match.get("field")
        }
    )

    matched_pages = sorted(
        {
            str(match.get("page_url"))
            for match in matches
            if match.get("page_url")
        }
    )

    return {
        "contract_version": "crawler.find_report.v1",
        "enabled": bool(query and query.strip()),
        "query": query,
        "case_sensitive": case_sensitive,
        "max_matches": max_matches,
        "find_scope": find_scope,
        "find_report": find_report,
        "match_count": len(matches),
        "matched_field_count": len(matched_fields),
        "matched_page_count": len(matched_pages),
        "matched_fields": matched_fields,
        "matched_pages": matched_pages,
        "matches": matches,
    }