from __future__ import annotations

from typing import Any

from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.tools.base import BaseTool
from src.domains.agentverse.tools.tool_manager_v1 import opts
from src.domains.agentverse.tools.tool_manager_v1.facts import ManagerFacts
from src.domains.agentverse.utils.canonicalize import canonicalize
from src.domains.agentverse.utils.coercion import coerce_val
from src.domains.agentverse.utils.strip_quotes import strip_quotes


class ToolManager:
    _last_used: dict[str, float] = {}

    def __init__(self, agent, tool_specs: list):
        self.agent = agent
        self._runtime_dependencies: dict[str, Any] = {}
        self._active_group_choice: dict[str, str] = {}
        self.tool_specs = tool_specs or []
        self._factories: dict[str, dict] = {}
        self._instances: dict[str, BaseTool] = {}
        self._tool_anchors: dict[str, list[str]] = {}
        self._specs: dict[str, Any] = {}

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[🧰 TOOL][🧬 BOOT] tool_bay_online",
            extra={
                "agent_id": getattr(agent, "id", None),
                "agent_name": getattr(agent, "name", None),
                "tool_spec_count": len(self.tool_specs),
            },
        )

        for s in self.tool_specs or []:
            if key := canonicalize(getattr(s, "key", None) or ""):
                self._specs[key] = s

        opts._prepare_factories(
            factories=self._factories,
            tool_anchor_map=self._tool_anchors,
            tool_specs=self.tool_specs,
        )

        # Built once: _factories is final after _prepare_factories and agent.workflows
        # never changes, so every execution can share the same object.
        self.facts = ManagerFacts.from_manager(self)

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[🧰 TOOL][🧬 BOOT] tool_bay_ready",
            extra={
                "agent_id": getattr(agent, "id", None),
                "agent_name": getattr(agent, "name", None),
                "factory_count": len(self._factories),
                "spec_count": len(self._specs),
            },
        )

    @staticmethod
    def validate_specs(specs: list):
        return opts._validate_specs(specs)
    
    def set_runtime_dependencies(
        self,
        dependencies: dict[str, Any] | None,
    ) -> None:
        self._runtime_dependencies = dict(
            dependencies or {}
        )

        for instance in self._instances.values():
            self._bind_runtime_dependencies(instance)

        AGENTVERSE_LOGGER.debug(
            LogDomain.TOOL_BAY,
            "[🧰 TOOL][🔌 RUNTIME] dependencies_bound",
            extra={
                "dependency_keys": sorted(
                    self._runtime_dependencies.keys()
                ),
                "existing_instances": sorted(
                    self._instances.keys()
                ),
            },
        )


    def _bind_runtime_dependencies(
        self,
        tool: BaseTool,
    ) -> None:
        for name, dependency in (
            self._runtime_dependencies.items()
        ):
            setattr(tool, name, dependency)


    @property
    def runtime_dependencies(self) -> dict[str, Any]:
        """
        Shared application-owned dependencies available to tool constructors.
        """
        return self._runtime_dependencies

    def _build_context(
        self, text: str = "", history=None, file=None, extra: dict | None = None
    ) -> dict:
        ctx = {
            "text": text or "",
            "history": history or [],
            "file": file,
            "user_id": getattr(self.agent, "user_id", None),
        }
        if extra:
            ctx.update(extra)
        return ctx

    def canonicalize_tool_ref(self, ref: str) -> str:
        if not ref:
            return ref

        resolved_key, _route = self.route(ref)
        return resolved_key or ref

    def _route_for(self, key: str) -> str:
        key_in = key
        route = opts.route_for_v1(self._factories, key)

        AGENTVERSE_LOGGER.debug(
            LogDomain.TOOL_BAY,
            "[🧰 TOOL][🧭 ROUTE] route_selected",
            extra={
                "requested_key": key_in,
                "resolved_key": key,
                "route": route,
            },
        )
        return route

    def list_tool_keys(self) -> list[str]:
        """Keys that may be invoked directly (the agent's chat surface).

        Excludes workflow-scoped tools: they are runnable by the workflow engine
        but attaching a workflow must never widen what chat can reach for. Use
        :meth:`all_tool_keys` for the full runtime inventory.
        """
        return [
            key
            for key, factory in self._factories.items()
            if (factory.get("scope") or "agent") != "workflow"
        ]

    def all_tool_keys(self) -> list[str]:
        """Every key this manager can construct, whatever its scope."""
        return list(self._factories.keys())

    def ensure_instance(
        self,
        key: str,
    ) -> BaseTool | None:
        inst = opts._ensure_tool_instance(
            self,
            key,
        )

        if inst is not None:
            self._bind_runtime_dependencies(inst)

        return inst

    async def execute(self, socket_emitter, key: str, **kwargs):
        """
        Public unified execution entrypoint. Delegate everything to execute_router.
        """
        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[🧰 TOOL][🚀 EXECUTE] execution_start",
            extra={
                "tool_key": key,
                "kwarg_keys": list(kwargs.keys()),
                "agent_id": getattr(self.agent, "id", None),
                "agent_name": getattr(self.agent, "name", None),
            },
        )

        return await opts._execute_router(
            socket_emitter=socket_emitter,
            manager=self,
            key=key,
            kwargs=kwargs,
            normalize_execute_inputs=opts._normalize_execute_inputs,
            pop_common_execute_kwargs=opts._pop_common_execute_kwargs,
            build_effective_context=opts._build_effective_context,
            apply_depth_guard=opts._apply_depth_guard,
            evaluate_policy=opts.evaluate_policy,
            route_for_key=self._route_for,
            execute_system_minimal=opts._execute_system_minimal,
            execute_invoker_path=opts._execute_invoker_path,
            prepare_pure_prep=opts._prepare_pure_prep,
            execute_pure_path=opts._execute_pure_path,
            collect_param_specs=opts._collect_param_specs,
            extract_cli_overrides=opts._extract_cli_overrides,
            strip_tool_anchor=opts._strip_tool_anchor,
            flag_re=opts._FLAG_RE,
            resolve_param_name=opts._resolve_param_name,
            strip_quotes=strip_quotes,
            coerce_val=coerce_val,
            apply_config_defaults=opts._apply_config_defaults,
            get_cfg_value=opts._get_cfg_value,
            maybe_tool_infer=opts._maybe_tool_infer,
        )
