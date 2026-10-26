"""Resource exposure describes catalogs, independently of invocation permissions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

ResourceExposure = Literal["public", "internal"]


@dataclass(frozen=True)
class ResourceClassification:
    exposure: ResourceExposure
    system: bool
    builtin: bool
    hidden: bool


def classify_resource(metadata: dict[str, Any]) -> ResourceClassification:
    """Normalize standard exposure and legacy flags without rewriting metadata.

    Explicit public exposure never clears legacy restrictions. Scope is consulted
    only as a legacy catalog hint; exposure itself grants no execution authority.
    """
    declared = metadata.get("exposure")
    if declared not in (None, "public", "internal"):
        raise ValueError(f"Invalid resource exposure: {declared!r}")
    tags = {str(tag).lower() for tag in metadata.get("tags") or []}
    cfg = metadata.get("config") or metadata.get("default_config") or {}
    supports = metadata.get("supports") or {}
    visibility = str(
        metadata.get("visibility") or (metadata.get("ui") or {}).get("visibility") or "public"
    ).lower()
    system = bool(
        "system" in tags
        or str(metadata.get("scope") or "").lower() == "system"
        or str(cfg.get("_scope") or "").lower() == "system"
        or supports.get("system_only")
        or metadata.get("is_system")
    )
    builtin = bool("builtin" in tags or metadata.get("is_builtin") or cfg.get("_builtin"))
    hidden = bool(visibility == "hidden" or cfg.get("_hidden") or metadata.get("hidden"))
    exposure: ResourceExposure = (
        "internal" if declared == "internal" or system or builtin or hidden else "public"
    )
    return ResourceClassification(exposure, system, builtin, hidden)
