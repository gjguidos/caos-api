from __future__ import annotations

import importlib
import inspect
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel as _BM

from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.tools.tool_manager_v1.dep_map import dep_map as _dep_map
from src.domains.agentverse.tools.tool_manager_v1.policy import evaluate_policy
from src.domains.agentverse.utils.canonicalize import canonicalize

BACKEND_ALIAS = {
    "llm": "llm",
    "image_client": "image",
    "video": "video",
    "audio": "audio",
    "transcription_client": "audio",
    "db": "db",
    "cache": "cache",
    "vectordb": "knowledge_db",
    "messaging": "messaging",
    "embedding": "embedding",
}


class ToolValidationError(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("\n".join(errors))
        self.errors = errors


def _coerce_cfg(
    schema_ref: str | None,
    cfg: dict,
    *,
    local_lookup: dict[str, Any] | None = None,
):
    if not schema_ref:
        return cfg

    try:
        cls = None
        if "." in schema_ref:
            mod_path, class_name = schema_ref.rsplit(".", 1)
            mod = importlib.import_module(mod_path)
            cls = getattr(mod, class_name, None)
        else:
            if local_lookup:
                cls = local_lookup.get(schema_ref)

        if cls and isinstance(cls, type) and issubclass(cls, _BM):
            return cls(**cfg)
    except Exception:
        return cfg

    return cfg


def validate_specs(
    specs: list,
    *,
    local_lookup: dict[str, Any] | None = None,
) -> tuple[list, set[str]]:
    """
    Normalize & validate tool specs without needing an Agent instance.
    Returns (normalized_specs, required_backend_kinds).
    Raises ToolValidationError on problems.
    """
    from src.domains.agentverse.registries.utils.utils import get_registry

    tool_reg = get_registry("tool")

    errors: list[str] = []
    normalized: list = []
    required: set[str] = set()

    AGENTVERSE_LOGGER.debug(
        LogDomain.TOOL_BAY,
        "[🧰 TOOL][🧪 VALIDATE] spec_validation_start",
        extra={
            "spec_count": len(specs or []),
        },
    )

    for spec in specs or []:
        is_dict = isinstance(spec, dict)

        key = spec.get("key") if is_dict else getattr(spec, "key", None)
        version = spec.get("version") if is_dict else getattr(spec, "version", None)
        config = (spec.get("config") if is_dict else getattr(spec, "config", None)) or {}

        # Tools are addressed by canonical key only.
        ref = key
        if not ref:
            errors.append("Tool spec missing 'key'")
            continue

        try:
            tool_cls = tool_reg.get(ref)
        except Exception:
            errors.append(f"Unknown tool: '{ref}'")
            continue

        if not tool_cls:
            errors.append(f"Unknown tool: '{ref}'")
            continue

        # Resolve canonical key, display name, and registry metadata
        canon_key = canonicalize(ref)
        info = tool_reg.get_info(canon_key)
        display_name = info.name if info else ref
        meta = (info.metadata or {}) if info else {}
        default_cfg = meta.get("config") or {}
        merged_cfg = {**default_cfg, **(config or {})}

        schema_ref = meta.get("config_schema")
        coerced_cfg = _coerce_cfg(schema_ref, merged_cfg, local_lookup=local_lookup)
        if isinstance(coerced_cfg, _BM):
            coerced_cfg = coerced_cfg.model_dump(exclude_none=True)

        required_deps = set(getattr(tool_cls, "required_dependencies", [])) | set(
            getattr(tool_cls, "required_deps", [])
        )
        for dep in required_deps:
            kind = BACKEND_ALIAS.get(dep)
            if kind:
                required.add(kind)

        # Preserve input type: AgentToolRef stays AgentToolRef, dicts stay dicts, ToolSpec stays ToolSpec
        from src.domains.agentverse.tools.spec import AgentToolRef

        if is_dict:
            normalized.append({"key": canon_key, "config": coerced_cfg})
        elif isinstance(spec, AgentToolRef):
            normalized.append(AgentToolRef(key=canon_key, config=coerced_cfg))
        else:
            normalized.append(
                spec.__class__(name=display_name, version=version, config=coerced_cfg)
            )

    if errors:
        AGENTVERSE_LOGGER.warning(
            LogDomain.TOOL_BAY,
            "[🧰 TOOL][❌ VALIDATE] spec_validation_failed",
            extra={
                "error_count": len(errors),
                "errors": errors,
            },
        )
        raise ToolValidationError(errors)

    AGENTVERSE_LOGGER.debug(
        LogDomain.TOOL_BAY,
        "[🧰 TOOL][✅ VALIDATE] spec_validation_complete",
        extra={
            "normalized_count": len(normalized),
            "required_backends": sorted(required),
        },
    )

    return normalized, required


def ensure_tool_instance(manager: Any, key: str):
    """
    Behavior-identical extraction of ToolManager.ensure_instance.
    Returns tool instance or None.
    """
    AGENTVERSE_LOGGER.debug(
        LogDomain.TOOL_BAY,
        "[🧰 TOOL][🧩 INSTANCE] ensure_start",
        extra={
            "tool_key": key,
            "agent_id": getattr(manager.agent, "id", None),
            "agent_name": getattr(manager.agent, "name", None),
        },
    )

    factory = manager._factories.get(key)

    if not factory:
        AGENTVERSE_LOGGER.warning(
            LogDomain.TOOL_BAY,
            "[🧰 TOOL][⚠️ INSTANCE] ensure_missing_factory",
            extra={"tool_key": key},
        )
        return None

    if key in manager._instances:
        AGENTVERSE_LOGGER.debug(
            LogDomain.TOOL_BAY,
            "[🧰 TOOL][⚡ INSTANCE] ensure_cache_hit",
            extra={"tool_key": key},
        )
        return manager._instances[key]

    veto_reason = evaluate_policy(manager.agent, manager._factories, key)
    if veto_reason:
        AGENTVERSE_LOGGER.warning(
            LogDomain.TOOL_BAY,
            "[🧰 TOOL][🚫 POLICY] ensure_policy_blocked",
            extra={
                "tool_key": key,
                "reason": veto_reason,
                "agent_id": getattr(manager.agent, "id", None),
                "agent_name": getattr(manager.agent, "name", None),
            },
        )
        return None

    if key in manager._instances:
        AGENTVERSE_LOGGER.debug(
            LogDomain.TOOL_BAY,
            "[🧰 TOOL][⚡ INSTANCE] ensure_cache_hit",
            extra={"tool_key": key},
        )
        return manager._instances[key]

    factory = manager._factories.get(key)
    if not factory:
        AGENTVERSE_LOGGER.warning(
            LogDomain.TOOL_BAY,
            "[🧰 TOOL][⚠️ INSTANCE] ensure_factory_lost",
            extra={"tool_key": key},
        )
        return None

    deps = _dep_map(manager.agent)

    ctor = factory["cls"].__init__
    allowed = set(inspect.signature(ctor).parameters.keys())
    init_kwargs = {k: v for k, v in deps.items() if k in allowed and v is not None}

    cfg = factory.get("config")
    if isinstance(cfg, SimpleNamespace):
        cfg = vars(cfg)
    elif isinstance(cfg, _BM):
        cfg = cfg.model_dump(exclude_none=True)

    init_kwargs["config"] = cfg

    AGENTVERSE_LOGGER.debug(
        LogDomain.TOOL_BAY,
        "[🧰 TOOL][🧩 INSTANCE] instantiate_prepare",
        extra={
            "tool_key": key,
            "init_arg_keys": list(init_kwargs.keys()),
            "runtime_dependency_keys": sorted(
                getattr(
                    manager,
                    "runtime_dependencies",
                    {},
                ).keys()
            ),
            "config_type": (
                type(cfg).__name__
                if cfg is not None
                else None
            ),
        },
    )

    try:
        inst = factory["cls"](**init_kwargs)

        # Bind application-owned AgentVerse runtime dependencies.
        runtime_dependencies = getattr(
            manager,
            "runtime_dependencies",
            {},
        ) or {}

        bind_runtime_dependencies = getattr(
            inst,
            "bind_runtime_dependencies",
            None,
        )

        if callable(bind_runtime_dependencies):
            bind_runtime_dependencies(
                runtime_dependencies
            )

        AGENTVERSE_LOGGER.debug(
            LogDomain.TOOL_BAY,
            "[🧰 TOOL][🔌 RUNTIME] dependencies_bound",
            extra={
                "tool_key": key,
                "instance_type": type(inst).__name__,
                "dependency_keys": sorted(
                    runtime_dependencies.keys()
                ),
            },
        )
        
        setattr(inst, "agent", manager.agent)
        setattr(inst, "agent_id", getattr(manager.agent, "id", None))
        setattr(inst, "collections", getattr(manager.agent, "collections", []) or [])

        on_attached = getattr(inst, "on_attached", None)
        if callable(on_attached):
            try:
                on_attached(manager.agent)
            except Exception:
                AGENTVERSE_LOGGER.exception(
                    LogDomain.TOOL_BAY,
                    "[🧰 TOOL][💥 INSTANCE] on_attached_failed",
                    extra={"tool_key": key},
                )

        manager._instances[key] = inst

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[🧰 TOOL][✅ INSTANCE] instantiate_complete",
            extra={
                "tool_key": key,
                "instance_type": type(inst).__name__,
                "agent_id": getattr(manager.agent, "id", None),
                "agent_name": getattr(manager.agent, "name", None),
            },
        )
        return inst

    except Exception as e:
        AGENTVERSE_LOGGER.exception(
            LogDomain.TOOL_BAY,
            "[🧰 TOOL][💥 INSTANCE] instantiate_failed",
            extra={
                "tool_key": key,
                "error_type": type(e).__name__,
                "error": str(e),
            },
        )
        return None
