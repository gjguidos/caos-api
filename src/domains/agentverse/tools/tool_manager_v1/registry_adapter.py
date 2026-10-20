from importlib import import_module
from typing import Any

from src.domains.agentverse.tools.tool_manager_v1.config import (
    maybe_coerce_config as _maybe_coerce_config,
)
from src.domains.agentverse.utils.canonicalize import canonicalize


def load_callable(dotted: str):
    mod, _, fn = dotted.rpartition(":")
    if not mod:
        mod, _, fn = dotted.rpartition(".")
    m = import_module(mod)
    return getattr(m, fn)


def prepare_factories(
    *,
    factories: dict[str, dict],
    tool_anchor_map: dict[str, list[str]],
    tool_specs: list[Any],
    include_mcp_tools: bool = False,
) -> None:
    """
    Populate ToolManager runtime lookup tables from tool_specs + ToolRegistry.

    Side effects (mutates in-place):
      - factories[key] = {"cls": ToolCls, "config": cfg_obj, "meta": meta, "scope": scope}
            - tool_anchor_map[key] = [key]

    ``factories`` is the *runnable* set: a key absent from it cannot be constructed,
    not merely not-permitted. Each entry carries the spec's ``scope``, which narrows
    that further - ``workflow``-scoped entries are runnable by the workflow engine but
    are not a direct-invocation surface (see ``ToolManager.list_tool_keys``).

    Args:
        include_mcp_tools: If True, also include every MCP proxy tool from the global registry.
            Keep this disabled for per-agent managers; configured tools are the authorization
            boundary.
    """
    # local import to avoid circular import problems during auto-routing
    from src.domains.agentverse.registries.utils.utils import get_registry

    tool_reg = get_registry("tool")

    factories.clear()
    tool_anchor_map.clear()

    for spec in tool_specs or []:
        key = canonicalize(getattr(spec, "key", None) or "")
        if not key:
            continue

        ToolCls = tool_reg.get(key)
        meta = tool_reg.get_metadata_for_ref(key)

        default_cfg = meta.get("config") or {}
        spec_cfg = getattr(spec, "config", {}) or {}
        merged_cfg = {**default_cfg, **spec_cfg}
        cfg_obj = _maybe_coerce_config(meta.get("config_schema"), merged_cfg)

        factories[key] = {
            "cls": ToolCls,
            "config": cfg_obj,
            "meta": meta,
            "scope": getattr(spec, "scope", "agent") or "agent",
        }
        tool_anchor_map[key] = [key]

    # ---- MCP Proxy tools (auto-include from registry) -----------------------------
    if include_mcp_tools:
        import logging

        _mcp_log = logging.getLogger("agentverse.tools.mcp_adapter")
        registry_items = tool_reg.items()
        _mcp_log.info(f"[MCP_ADAPTER] Scanning registry: {len(registry_items)} total keys")

        mcp_count = 0
        for item in registry_items:
            mcp_key = item.key
            meta = item.metadata or {}
            # Skip if already added from tool_specs or not an MCP tool
            if mcp_key in factories:
                continue
            if meta.get("source") != "mcp_proxy":
                continue

            _mcp_log.info(f"[MCP_ADAPTER] Found MCP tool: {mcp_key}")

            try:
                ToolCls = tool_reg.get(mcp_key)
            except Exception as e:
                _mcp_log.warning(f"[MCP_ADAPTER] Failed to resolve {mcp_key}: {e}")
                continue  # Skip if unresolvable

            cfg_obj = _maybe_coerce_config(meta.get("config_schema"), meta.get("config") or {})
            factories[mcp_key] = {
                "cls": ToolCls,
                "config": cfg_obj,
                "meta": meta,
                "scope": "agent",
            }
            mcp_count += 1
            tool_anchor_map[mcp_key] = [mcp_key]

        _mcp_log.info(f"[MCP_ADAPTER] Added {mcp_count} MCP tools to ToolManager factories")


def meta_for_factories(self, specs: dict[str, Any], ref: str) -> dict[str, Any]:
    # cache per tool key for this manager
    cref = canonicalize(ref)
    cached = self._meta_cache.get(cref)
    if cached is not None:
        return cached

    # factory meta is already resolved once in prepare_factories()
    factory = self._factories.get(cref)
    meta = (factory.get("meta") or {}).copy() if factory else {}

    self._meta_cache[cref] = meta
    return meta
