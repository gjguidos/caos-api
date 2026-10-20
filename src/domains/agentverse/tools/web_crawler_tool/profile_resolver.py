from __future__ import annotations

from typing import Any

from src.domains.agentverse.tools.web_crawler_tool.output_profiles import (
    OUTPUT_PROFILES,
    normalize_output_profile,
)

PROFILE_CONTROLLED_FIELDS = {
    "report",
    "output_format",
    "include_raw_pages",
    "include_security_audit",
    "include_errors",
    "max_pages",
    "max_depth",
    "include_links",
    "same_domain_only",
}


REQUEST_OVERRIDE_FIELDS = PROFILE_CONTROLLED_FIELDS | {
    "mode",
    "crawl_strategy",
    "timeout_seconds",
    "max_chars_per_page",
}

def _has_meaningful_value(value: Any) -> bool:
    if value is None:
        return False

    if isinstance(value, str):
        return bool(value.strip())

    if isinstance(value, list | tuple | set | dict):
        return bool(value)

    return True

def resolve_web_crawler_profile(
    *,
    output_profile: str | None,
    request_overrides: dict[str, Any] | None = None,
    custom_profiles: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    request_overrides = request_overrides or {}
    custom_profiles = custom_profiles or {}

    profile_name = normalize_output_profile(output_profile)

    profiles = {
        **OUTPUT_PROFILES,
        **custom_profiles,
    }

    if profile_name not in profiles:
        allowed = ", ".join(sorted(profiles))
        raise ValueError(
            f"Unsupported output_profile: {output_profile!r}. "
            f"Allowed profiles: {allowed}"
        )

    resolved = dict(profiles[profile_name])

    for field in REQUEST_OVERRIDE_FIELDS:
        value = request_overrides.get(field)

        if _has_meaningful_value(value):
            resolved[field] = value

    resolved["output_profile"] = profile_name
    resolved["profile_source"] = (
        "custom" if profile_name in custom_profiles else "builtin"
    )

    return resolved