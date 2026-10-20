from __future__ import annotations

ALL_REPORTS: set[str] = {
    "metadata",
    "crawl_graph",
    "html_components",
    "seo",
    "links",
    "forms",
    "content_blocks",
    "detected_html_blocks",
    "snapshots",
    "documents",
    "media",
    "asset_light",
    "find",
    "filters",
    "crawl_summary",
}

OBLIGATORY_REPORTS: set[str] = {
    "metadata",
    "crawl_summary",
}

DEFAULT_REPORTS: set[str] = {
    "crawl_summary",
    "media",
    "documents",
    "find",
    "filters",
}

REPORT_ALIASES: dict[str, str] = {
    "all": "__all__",
    "*": "__all__",
    "summary": "crawl_summary",
    "graph": "crawl_graph",
    "html": "html_components",
    "components": "html_components",
    "content": "content_blocks",
    "blocks": "content_blocks",
    "detected": "detected_html_blocks",
    "snapshot": "snapshots",
    "doc": "documents",
    "docs": "documents",
    "document": "documents",
    "images": "media",
    "image": "media",
    "links_inventory": "links",
    "forms_inventory": "forms",
}


def normalize_requested_reports(
    report: str | list[str] | tuple[str, ...] | set[str] | None,
    *,
    default: set[str] | None = None,
) -> set[str]:
    if report is None:
        return set(default or DEFAULT_REPORTS) | OBLIGATORY_REPORTS

    if isinstance(report, str):
        raw_items = [
            item.strip().lower()
            for item in report.replace(";", ",").split(",")
            if item.strip()
        ]
    else:
        raw_items = [
            str(item).strip().lower()
            for item in report
            if str(item).strip()
        ]

    if not raw_items:
        return set(default or DEFAULT_REPORTS) | OBLIGATORY_REPORTS

    selected: set[str] = set()

    for raw in raw_items:
        key = REPORT_ALIASES.get(raw, raw)

        if key == "__all__":
            return set(ALL_REPORTS) | OBLIGATORY_REPORTS

        if key not in ALL_REPORTS:
            allowed = ", ".join(sorted(ALL_REPORTS))
            raise ValueError(
                f"Unsupported report: {raw!r}. Allowed reports: {allowed}"
            )

        selected.add(key)

    return selected | OBLIGATORY_REPORTS