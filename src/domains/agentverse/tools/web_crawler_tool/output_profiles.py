from __future__ import annotations

from typing import Any

OUTPUT_PROFILES: dict[str, dict[str, Any]] = {
    "general": {
        "report": "crawl_summary,links,documents,media,seo",
        "output_format": "markdown",
        "include_raw_pages": False,
        "include_security_audit": False,
        "include_errors": True,
        "max_pages": 3,
        "max_depth": 1,
        "include_links": True,
        "same_domain_only": True,
    },
    "media_light": {
        "report": "crawl_summary,asset_light",
        "output_format": "json",
        "include_raw_pages": False,
        "include_security_audit": False,
        "include_errors": True,
        "max_pages": 100,
        "max_depth": 0,
        "include_links": False,
        "same_domain_only": True,
    },
    "workflow": {
        "report": "crawl_summary,media,documents,content_blocks,find,filters",
        "output_format": "json",
        "include_raw_pages": False,
        "include_security_audit": False,
        "include_errors": True,
        "max_pages": 5,
        "max_depth": 1,
        "include_links": True,
        "same_domain_only": True,
    },
    "admin": {
        "report": "crawl_summary,crawl_graph,seo,media,documents,links,forms,filters",
        "output_format": "markdown",
        "include_raw_pages": False,
        "include_security_audit": True,
        "include_errors": True,
        "max_pages": 10,
        "max_depth": 2,
        "include_links": True,
        "same_domain_only": True,
    },
    "seo": {
        "report": "crawl_summary,seo,links,content_blocks,detected_html_blocks",
        "output_format": "markdown",
        "include_raw_pages": False,
        "include_security_audit": False,
        "include_errors": True,
        "max_pages": 10,
        "max_depth": 1,
        "include_links": True,
        "same_domain_only": True,
    },
    "media": {
        "report": "crawl_summary,media,filters",
        "output_format": "markdown",
        "include_raw_pages": False,
        "include_security_audit": False,
        "include_errors": True,
        "max_pages": 5,
        "max_depth": 1,
        "include_links": True,
        "same_domain_only": True,
    },
    "documents": {
        "report": "crawl_summary,documents,filters",
        "output_format": "json",
        "include_raw_pages": False,
        "include_security_audit": False,
        "include_errors": True,
        "max_pages": 5,
        "max_depth": 1,
        "include_links": True,
        "same_domain_only": True,
    },
    "full": {
        "report": "all",
        "output_format": "markdown",
        "include_raw_pages": False,
        "include_security_audit": True,
        "include_errors": True,
        "max_pages": 10,
        "max_depth": 2,
        "include_links": True,
        "same_domain_only": True,
    },
    "debug": {
        "report": "all",
        "output_format": "markdown",
        "include_raw_pages": True,
        "include_security_audit": True,
        "include_errors": True,
        "max_pages": 3,
        "max_depth": 1,
        "include_links": True,
        "same_domain_only": True,
    },
}

OUTPUT_PROFILE_UI_OPTIONS: list[dict[str, Any]] = [
    {
        "value": "general",
        "label": "General",
        "description": "Light, readable crawl for normal use.",
        "recommended_for": ["quick inspection", "normal chat use"],
    },
    {
        "value": "media_light",
        "label": "Media (lightweight)",
        "description": "Fast media inventory with minimal page processing.",
        "recommended_for": [
            "large sitemap crawls",
            "image inventory",
            "media ownership",
        ],
    },
    {
        "value": "seo",
        "label": "SEO",
        "description": "SEO, links, content blocks and HTML structure.",
        "recommended_for": ["SEO review", "page structure"],
    },
    {
        "value": "workflow",
        "label": "Workflow",
        "description": "Compact structured output for workflow handoff.",
        "recommended_for": ["automation", "tool chaining"],
    },
    {
        "value": "media",
        "label": "Media",
        "description": "Media inventory and filters.",
        "recommended_for": ["images", "assets", "media ownership"],
    },
    {
        "value": "documents",
        "label": "Documents",
        "description": "Document inventory and filters.",
        "recommended_for": ["PDFs", "files", "document extraction"],
    },
    {
        "value": "admin",
        "label": "Admin",
        "description": "Operational view with crawl graph and diagnostics.",
        "recommended_for": ["debugging", "admin inspection"],
    },
    {
        "value": "full",
        "label": "Full",
        "description": "All reports without raw page payloads.",
        "recommended_for": ["complete review"],
    },
    {
        "value": "debug",
        "label": "Debug",
        "description": "All reports with raw pages and diagnostics.",
        "recommended_for": ["developer debugging"],
    },
]

def normalize_output_profile(value: str | None) -> str:
    profile = (value or "general").strip().lower()

    aliases = {
        "default": "general",
        "basic": "general",
        "light": "general",
        "wf": "workflow",
        "automation": "workflow",
        "ops": "admin",
        "administrator": "admin",
        "image": "media",
        "images": "media",
        "doc": "documents",
        "docs": "documents",
        "document": "documents",
        "fast_media": "media_light",
        "media_fast": "media_light",
        "all": "full",
    }

    profile = aliases.get(profile, profile)

    if profile not in OUTPUT_PROFILES:
        allowed = ", ".join(sorted(OUTPUT_PROFILES))
        raise ValueError(
            f"Unsupported output_profile: {value!r}. Allowed profiles: {allowed}"
        )

    return profile


def resolve_output_profile(
    *,
    output_profile: str | None,
    report: str | None,
    output_format: str | None,
    include_raw_pages: bool | None,
    include_security_audit: bool | None,
    include_errors: bool | None,
    max_pages: int | None = None,
    max_depth: int | None = None,
    include_links: bool | None = None,
    same_domain_only: bool | None = None,
) -> dict[str, Any]:
    profile_name = normalize_output_profile(output_profile)
    resolved = dict(OUTPUT_PROFILES[profile_name])

    if report is not None:
        resolved["report"] = report

    if output_format is not None:
        resolved["output_format"] = output_format

    if include_raw_pages is not None:
        resolved["include_raw_pages"] = include_raw_pages

    if include_security_audit is not None:
        resolved["include_security_audit"] = include_security_audit

    if include_errors is not None:
        resolved["include_errors"] = include_errors

    if max_pages is not None:
        resolved["max_pages"] = max_pages

    if max_depth is not None:
        resolved["max_depth"] = max_depth

    if include_links is not None:
        resolved["include_links"] = include_links

    if same_domain_only is not None:
        resolved["same_domain_only"] = same_domain_only

    resolved["output_profile"] = profile_name
    return resolved