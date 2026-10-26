from __future__ import annotations

from typing import Any

from src.domains.agentverse.entities.resource_exposure import classify_resource
from src.domains.agentverse.exceptions import RegistrationError
from src.domains.agentverse.logging.logger_v1 import log_nerv_hq
from src.domains.agentverse.registries.base import Registry
from src.domains.agentverse.registries.utils.register_registry import register_registry
from src.domains.agentverse.tools.base import BaseTool
from src.domains.agentverse.tools.spec import ToolOutputSpec, ToolSpec
from src.domains.agentverse.utils.canonicalize import require_canonical_key


@register_registry(name="Tool Registry", icon="🧰")
class ToolRegistry(Registry[type[BaseTool]]):
    name = "tool"
    description = "Registry for EVA tools"

    def __init__(self):
        super().__init__(name=self.name)

    @staticmethod
    def _normalize_outputs(meta: dict[str, Any], *, tool: str) -> None:
        """Validate and canonicalize ``meta["outputs"]`` in place, at registration.

        Both registration paths converge here (``bind_spec`` funnels its
        ``ToolSpec.metadata`` through ``register_tool``), so this is the single
        choke point where a malformed output declaration fails loudly at import
        time instead of degrading to an empty binding namespace at run time. The
        stored value is a list of plain, validated dicts, keeping the metadata
        JSON-serializable for the tool-listing API.
        """
        raw = meta.get("outputs")
        if not raw:
            return
        try:
            meta["outputs"] = [ToolOutputSpec(**o).model_dump() for o in raw]
        except Exception as exc:
            raise RegistrationError(
                message=f"Tool `{tool}` has an invalid `outputs` declaration: {exc}",
                details={"tool": tool},
            ) from exc

    def _validate_component(self, component: type) -> None:
        if not issubclass(component, BaseTool):
            raise TypeError(f"Component `{component.__name__}` must inherit from BaseTool")
        for attr in ("name", "version", "parameters"):
            if not hasattr(component, attr):
                raise RegistrationError(
                    message=f"Tool `{component.__name__}` missing `{attr}` attribute",
                    details={"component": component.__name__},
                )

    def bind_spec(self, spec: ToolSpec):
        """
        Register a ToolSpec-described tool class. Thin wrapper over ``register_tool``:
        the spec's fields become the registration args, so the base registry is the
        single source of truth (no separate spec store).
        """
        # Skip disabled tools - return no-op decorator
        if not getattr(spec, "enabled", True):
            log_nerv_hq(f"[🚫 TOOL DISABLED] Skipping registration: {spec.key or spec.name}")

            def _noop_decorator(cls: type[BaseTool]) -> type[BaseTool]:
                return cls

            return _noop_decorator

        # capture to avoid late binding issues in nested function
        _register_tool = self.register_tool

        def _decorator(cls: type[BaseTool]) -> type[BaseTool]:
            deco = _register_tool(
                key=getattr(spec, "key", None) or getattr(cls, "key", None),
                name=spec.name or getattr(cls, "name", cls.__name__),
                description=spec.description or getattr(cls, "__doc__", "") or "",
                version=spec.version or getattr(cls, "version", "1.0.0"),
                metadata=spec.registration_metadata(),
            )
            return deco(cls)

        return _decorator

    # -------- Registration API with canonical key --------
    def register_tool(
        self,
        *,
        key: str,  # canonical (e.g. "datetime")
        name: str,  # human label (e.g. "Datetime Tool")
        description: str | None = None,
        version: str | None = None,
        metadata: dict[str, Any] | None = None,
        component: type[BaseTool] | None = None,
    ):
        """
        Decorator that registers a tool class under a human name with a canonical key.
        Usage:
            @tool_registry.register_tool(key="datetime", name="Datetime Tool")
            class DateTimeTool(BaseTool): ...
        """
        # Validated, not coerced: an author-provided key that is not already canonical is
        # a typo, and surfaces here at import time. Callers deriving keys from data they
        # do not control (see `mcp_tool_source`) canonicalize before calling.
        canon_key = require_canonical_key(key, kind="Tool", name=name)

        def _decorator(cls: type[BaseTool]) -> type[BaseTool]:
            self._validate_component(cls)

            display_name = name or getattr(cls, "name", None)
            if not display_name:
                raise RegistrationError(message=f"Tool `{cls.__name__}` must have a display name")

            # -------- class-derived defaults --------
            cls_caps = getattr(cls, "capabilities", None)
            cls_def = getattr(cls, "default_config", None)
            params_schema = self._parameters_to_jsonschema(getattr(cls, "parameters", {}))

            # -------- incoming decorator metadata (preserve as authoritative) --------
            incoming_meta: dict[str, Any] = dict(metadata or {})

            # -------- compose final metadata (defaults first, then incoming overrides) --------
            base_meta: dict[str, Any] = {
                "key": canon_key,
                "capabilities": list(cls_caps or []),
                "default_config": dict(cls_def or {}),
                "parameters_schema": params_schema,  # generated JSON schema for params (for FE forms)
            }

            # Incoming metadata wins (keeps rich UI fields: ui, examples, supports, etc.)
            meta: dict[str, Any] = {**base_meta, **incoming_meta}

            if "config" not in meta:
                meta["config"] = dict(meta.get("default_config") or {})

            classify_resource(meta)  # validate explicit exposure before registration
            self._normalize_outputs(meta, tool=display_name)

            # -------- register into base registry --------
            # Base `register` owns the key contract: last write under a key wins.
            super(ToolRegistry, self).register(
                name=display_name,
                description=description or getattr(cls, "__doc__", ""),
                version=version or getattr(cls, "version", "1.0.0"),
                metadata=meta,
                key=canon_key,
            )(cls)

            return cls

        return _decorator if component is None else _decorator(component)

    def get_metadata_for_ref(self, ref: str) -> dict[str, Any]:
        """
        Get registry metadata for a tool key.

        Returns an empty dict if the tool is unknown or has no metadata.
        """
        info = self.get_info(ref)
        return (info.metadata or {}).copy() if info else {}

    # -------- JSON Schema helpers --------
    @staticmethod
    def _parameters_to_jsonschema(params: dict[str, Any]) -> dict[str, Any]:
        """
        Convert legacy `BaseTool.parameters` to a minimal JSON Schema for UI forms.
        Expected shape:
          parameters = {
            "op": {"type":"string", "description":"...", "required": False, "default": "now"}
          }
        """
        props: dict[str, Any] = {}
        required: list[str] = []
        for name, spec in (params or {}).items():
            t = (spec.get("type") or "string").lower()
            # map a few synonyms
            if t in {"int", "integer"}:
                t = "integer"
            if t in {"float", "double", "number"}:
                t = "number"
            if t in {"bool", "boolean"}:
                t = "boolean"
            if t not in {"string", "integer", "number", "boolean", "object", "array"}:
                t = "string"
            entry = {"type": t}
            if "description" in spec:
                entry["description"] = spec["description"]
            if "default" in spec:
                entry["default"] = spec["default"]
            if "enum" in spec:
                entry["enum"] = list(spec["enum"])
            props[name] = entry
            if spec.get("required"):
                required.append(name)
        schema: dict[str, Any] = {"type": "object", "properties": props}
        if required:
            schema["required"] = required
        return schema

    def list_public(
        self,
        *,
        include_system: bool = False,
        include_builtin: bool = False,
        include_hidden: bool = False,
        allowed_tags: list[str] | None = None,
        blocked_tags: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Return tools intended for the frontend catalog.

        Defaults:
          • hides tools tagged as 'system' or 'builtin'
          • hides tools with visibility == 'hidden' or config['_hidden'] == True

        Heuristics also detect internal tools that lack explicit tags:
          • meta.scope == 'system' (or config['_scope'] == 'system')
          • meta.supports.system_only == True
          • meta.is_system / meta.is_builtin booleans
        """
        allowed_tags = {t.lower() for t in (allowed_tags or [])}
        blocked_tags = {t.lower() for t in (blocked_tags or [])}
        out = []

        def _lower_set(x):
            return {str(v).lower() for v in (x or [])}

        for item in self.items():
            key = item.key
            display_name = item.name
            meta = (item.metadata or {}).copy()

            # Normalize helpers
            tags = _lower_set(meta.get("tags"))
            classification = classify_resource(meta)
            supports = meta.get("supports") or {}
            # Explicit internal exposure has no public catalog opt-in. Legacy
            # include flags retain their existing behavior for trusted consumers.
            if meta.get("exposure") == "internal":
                continue
            if classification.hidden and not include_hidden:
                continue
            if classification.system and not include_system:
                continue
            if classification.builtin and not include_builtin:
                continue

            # Optional tag allow/deny lists (operate on normalized tags)
            if allowed_tags and tags.isdisjoint(allowed_tags):
                continue
            if blocked_tags and not tags.isdisjoint(blocked_tags):
                continue

            out.append(
                {
                    "key": key,
                    "name": display_name,
                    "description": item.description or "",
                    "version": item.version,
                    "capabilities": meta.get("capabilities", []),
                    "default_config": meta.get("default_config", {}),
                    "config": meta.get("config", meta.get("default_config", {})),
                    "config_schema": meta.get("config_schema"),
                    "parameters_schema": meta.get("parameters_schema", {}),
                    "ui": meta.get("ui", {}),
                    "outputs": meta.get("outputs", []),
                    "supports": supports,
                    "tool_class": meta.get("tool_class"),
                    "tags": list(tags),
                    "requires_collection": meta.get("requires_collection", False),
                }
            )
        return out
