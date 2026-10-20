# src/domains/agentverse/tools/tool_manager_v1/routing.py
from __future__ import annotations  # noqa: I001


def route_for_v1(factories, key: str) -> str:
    meta = (factories.get(key, {}) or {}).get("meta") or {}
    route = str(meta.get("route") or "").strip().lower()

    if not route:
        raise ValueError(f"Tool '{key}' missing meta.route")

    if route in {"pure", "invoker", "proxy", "llm_only"}:
        return route

    # hard fail is better during refactor:
    raise ValueError(f"Tool '{key}' has invalid meta.route='{route}'")
