from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel


# ------------------------------------------------------------------ #
#  Pretty helpers
# ------------------------------------------------------------------ #
def _fmt(v: Any) -> str:
    if isinstance(v, list):
        return ", ".join(map(str, v))
    if isinstance(v, Mapping):
        return "; ".join(f"{k}: {v}" for k, v in v.items())
    return str(v)


def _title(s: str) -> str:
    return s.replace("_", " ").title()


# ------------------------------------------------------------------ #
#  PUBLIC API
# ------------------------------------------------------------------ #
def generate_personality_context(
    raw: dict[str, Any] | BaseModel,
    *,
    expanded: bool = False,
) -> str:
    """
    Build an LLM-ready context string from any personality payload.

    Parameters
    ----------
    raw : dict  |  BaseModel
        • A delta dict coming from the UI, or
        • A Pydantic model that *may* implement `.minimal_dump()`.
    expanded : bool
        False → include only the keys present in the input
        True  → include every non-null key in the model_dump()
    """

    # -------- 1 · obtain a plain dict --------------------------------
    if isinstance(raw, BaseModel):
        if not expanded and hasattr(raw, "minimal_dump"):
            data: dict[str, Any] = raw.minimal_dump()
        else:
            data = raw.model_dump(exclude_none=True)
    else:
        data = raw  # already a dict sent by the client

    # -------- 2 · headline ------------------------------------------
    name = (
        data.get("name")
        or data.get("basic_profile", {}).get("name")
        or data.get("identity_profile", {}).get("name")
        or "Unnamed EVA"
    )
    description = (
        data.get("description")
        or data.get("basic_profile", {}).get("description")
        or data.get("identity_profile", {}).get("description")
        or "No description provided"
    )
    created_at = data.get("identity_profile", {}).get("created_at") or datetime.now(UTC).isoformat()

    lines: list[str] = [
        f"# Personality Context for {name}",
        f"Description: {description}",
        f"Created At: {created_at}",
        "",
        "## Profile Sections",
    ]

    # -------- 3 · iterate dict --------------------------------------
    loose_scalars: dict[str, Any] = {}
    for k, v in data.items():
        if k == "agent_soul_profile_metadata":
            continue  # skip metadata entirely

        if isinstance(v, Mapping):
            if not v:
                continue
            lines.append(f"\n### {_title(k)}")
            for sub_k, sub_v in v.items():
                if sub_v is not None:
                    lines.append(f"- {_title(sub_k)}: {_fmt(sub_v)}")
        else:
            loose_scalars[k] = v

    if loose_scalars:
        lines.append("\n### Extra Traits")
        for k, v in loose_scalars.items():
            lines.append(f"- {_title(k)}: {_fmt(v)}")

    return "\n".join(lines)
