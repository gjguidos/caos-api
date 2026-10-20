"""
AgentFactory for creating agent entities.

This factory focuses on the core responsibility of creating Agent entities
from configuration requests, with clear validation and simple logic.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import re
import unicodedata
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastapi import Request

from src.domains.agentverse.agents.base import BaseAgent
from src.domains.agentverse.entities.agent import Agent
from src.domains.agentverse.entities.agent_request import AgentRequest
from src.domains.agentverse.events.socket_contract import LogLevel, LogStage
from src.domains.agentverse.exceptions import AgentValidationError
from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.personalities.utils.resolve_personality import resolve_personality
from src.domains.agentverse.registries import agent_registry_instance as agent_registry
from src.domains.agentverse.registries import role_registry_instance as role_registry
from src.domains.agentverse.registries import tool_registry_instance as tool_registry
from src.domains.agentverse.registries import (
    tool_runtime_dependency_registry_instance as tool_runtime_dependency_registry,
)
from src.domains.agentverse.tools.spec import AgentToolRef, ToolScope, ToolSpec
from src.domains.agentverse.utils.canonicalize import canonicalize

if TYPE_CHECKING:
    # Import-time cycle: WorkflowService reaches into tools.workflow_tool, which
    # reaches back here. Only the annotation is needed.
    from src.domains.agentverse.services.workflow_service import WorkflowService

# Tools every agent gets regardless of role or request. Display names and config come
# from the registry at expansion time, so only the keys live here.
CORE_TOOL_KEYS = ("chat", "datetime", "cta")

# ========================== HELPER CLASSES ==========================


@dataclass
class CognitiveResources:
    """Container for agent runtime resources (LLM, audio, db, etc.)"""

    llm: Any
    audio: Any
    db: Any
    cache: Any
    vectordb: Any
    messaging: Any
    image: Any = None
    video: Any = None
    http: dict[str, Any] | None = None
    rest: Any = None
    public_settings: dict[str, Any] | None = None
    queue_service: Any = None


class AgentFactory:
    """
    Factory for creating agent entities.

    This factory handles:
    - Generating unique agent IDs
    - Creating chat URLs
    - Generating DNA sequences
    - Validating roles and tools
    - Creating system names from display names
    """

    def __init__(self, orchestrator=None, workflow_service: WorkflowService | None = None):
        self.orchestrator = orchestrator
        self.workflow_service = workflow_service

    async def create_agent(self, socket_emitter, agent_config: AgentRequest) -> Agent:
        AGENTVERSE_LOGGER.info(
            LogDomain.EVANGELION_BAY,
            "[🏭 FACTORY][🏗️ CREATE] create_start",
            extra={
                "agent_name": agent_config.name,
                "agent_type": agent_config.type,
                "creator_user_id": agent_config.user_id,
            },
        )
        await socket_emitter.log(
            f"Configuring EVA unit '{agent_config.name}'...",
            level=LogLevel.INFO,
            stage=LogStage.CONFIGURE,
        )

        AGENTVERSE_LOGGER.debug(
            LogDomain.EVANGELION_BAY,
            "[🏭 FACTORY][🧠 ROLE] role_resolve_start",
            extra={"agent_name": agent_config.name, "role_input": agent_config.role.name},
        )
        role_spec = self._resolve_role(agent_config.role.name)
        # persist the canonical key - it is what addresses the role registry on reload
        canonical_role_name = role_spec.key

        AGENTVERSE_LOGGER.info(
            LogDomain.EVANGELION_BAY,
            "[🏭 FACTORY][🧠 ROLE] role_resolve_complete",
            extra={"agent_name": agent_config.name, "role_name": canonical_role_name},
        )

        agent_id = self._generate_agent_id()
        chat_url = self._generate_chat_url(agent_id)
        dna_sequence = self._generate_dna_sequence(agent_config)

        AGENTVERSE_LOGGER.debug(
            LogDomain.EVANGELION_BAY,
            "[🏭 FACTORY][🧬 ID] identity_generated",
            extra={
                "agent_name": agent_config.name,
                "agent_id": agent_id,
                "chat_url": chat_url,
            },
        )

        AGENTVERSE_LOGGER.debug(
            LogDomain.EVANGELION_BAY,
            "[🏭 FACTORY][🛠️ TOOLS] tool_validation_start",
            extra={
                "agent_name": agent_config.name,
                "tool_keys": [t.key for t in agent_config.tools],
            },
        )
        self._validate_tools(agent_config.tools)
        self._validate_role_required_tools(role_spec, agent_config.tools)

        AGENTVERSE_LOGGER.debug(
            LogDomain.EVANGELION_BAY,
            "[🏭 FACTORY][🛠️ TOOLS] tool_validation_complete",
            extra={
                "agent_name": agent_config.name,
                "role_name": canonical_role_name,
            },
        )

        rule_pack_ids = list(agent_config.rule_pack_ids or [])
        if not rule_pack_ids and agent_config.role:
            rule_pack_ids = list(agent_config.role.rule_packs or [])

        self._validate_tool_rulepack_requirements(
            tools=agent_config.tools,
            rule_pack_ids=rule_pack_ids,
        )

        AGENTVERSE_LOGGER.debug(
            LogDomain.EVANGELION_BAY,
            "[🏭 FACTORY][📦 RULES] rulepacks_validated",
            extra={
                "agent_name": agent_config.name,
                "rule_pack_ids": rule_pack_ids,
            },
        )

        agent = Agent(
            id=agent_id,
            creator=agent_config.user_id,
            name=agent_config.name,
            system_name=self._to_system_name(agent_config.name),
            role_name=canonical_role_name,
            personality_key=agent_config.personality_key,
            prompt=agent_config.prompt,
            type=agent_config.type,
            chat_url=chat_url,
            llm_type=agent_config.llm_type,
            audio_type=agent_config.audio_type,
            image_type=agent_config.image_type,
            video_type=agent_config.video_type,
            db_type=agent_config.db_type,
            cache_type=agent_config.cache_type,
            knowledge_db_type=agent_config.knowledge_db_type,
            embedding_type=agent_config.embedding_type,
            messaging_type=agent_config.messaging_type,
            tools=agent_config.tools,
            workflows=list(agent_config.workflows or []),
            rule_pack_ids=rule_pack_ids,
            dna_sequence=dna_sequence,
            visibility=agent_config.visibility,
            whitelisted_groups=agent_config.whitelisted_groups,
        )

        await socket_emitter.log(
            f"EVA unit '{agent.name}' configured and ready.",
            level=LogLevel.SUCCESS,
            stage=LogStage.CONFIGURE,
        )

        AGENTVERSE_LOGGER.info(
            LogDomain.EVANGELION_BAY,
            "[🏭 FACTORY][🏁 CREATE] create_complete",
            extra={
                "agent_id": agent.id,
                "agent_name": agent.name,
                "role_name": agent.role_name,
                "agent_type": agent.type,
            },
        )

        return agent

    # ========================== ROLE MANAGEMENT ==========================
    
    def _resolve_role(self, role_name: str | None):
        """Resolve a role reference to its RoleSpec, defaulting to the general role."""
        try:
            key = role_registry.resolve_key(role_name or "general", kind="role")
        except ValueError as e:
            AGENTVERSE_LOGGER.warning(
                LogDomain.EVANGELION_BAY,
                "[🏭 FACTORY][🧠 ROLE] role_resolve_failed",
                extra={"role_input": role_name},
            )
            raise ValueError(f"Cannot create agent: unknown role '{role_name}'") from e

        return role_registry.get_role(key)

    # ========================== TOOL VALIDATION ==========================
    
    def _validate_tools(self, tools: list[AgentToolRef] | None) -> None:
        if not tools:
            return

        missing: list[str] = []
        for ref in tools:
            try:
                tool_registry.get(ref.key)
            except Exception:
                missing.append(ref.key or "<unknown>")

        if missing:
            AGENTVERSE_LOGGER.warning(
                LogDomain.EVANGELION_BAY,
                "[🏭 FACTORY][🛠️ TOOLS] unknown_tools_requested",
                extra={"missing_tools": missing},
            )
            raise AgentValidationError(
                f"Unknown tools requested: {', '.join(missing)}",
                data={"missing_tools": missing},
            )

    def _validate_role_required_tools(
        self, role_spec, agent_tools: list[AgentToolRef] | None
    ) -> None:
        """Every tool key the role declares required must be present on the agent.

        ``required_tools`` is either a list of keys or a logical-name -> key mapping;
        iterating it yields the keys in both shapes.
        """
        req = (getattr(role_spec, "capabilities", {}) or {}).get("required_tools") or {}
        required = {canonicalize(entry) for entry in req if entry}
        if not required:
            return

        have = {canonicalize(t.key) for t in agent_tools or [] if t.key}
        missing = sorted(required - have)
        if not missing:
            return

        missing_names = [self._tool_display_name(key) for key in missing]

        AGENTVERSE_LOGGER.warning(
            LogDomain.EVANGELION_BAY,
            "[🏭 FACTORY][🛠️ TOOLS] required_tools_missing",
            extra={
                "role_name": role_spec.name,
                "missing_tools": missing,
                "missing_tool_names": missing_names,
            },
        )
        raise AgentValidationError(
            f"Role '{role_spec.name}' requires the following tools, which are missing: "
            f"{', '.join(missing_names)}",
            data={
                "role_name": role_spec.name,
                "missing_tools": missing,
                "missing_tool_names": missing_names,
            },
        )

    @staticmethod
    def _tool_display_name(tool_key: str) -> str:
        """Human label for a tool key, falling back to the key when unregistered."""
        info = tool_registry.get_info(tool_key)
        return (getattr(info, "name", None) or tool_key) if info else tool_key

    def _get_rulepack_requirement_for_tool(self, tool_key: str) -> dict[str, Any]:
        meta = tool_registry.get_metadata_for_ref(tool_key)

        required_flag = bool(meta.get("required_rulepacks"))
        if not required_flag:
            return {"required": False, "min": 0, "hints": []}

        min_required = meta.get("min_rulepacks")
        try:
            min_required = int(min_required) if min_required is not None else 1
        except (TypeError, ValueError):
            min_required = 1

        hints_raw = meta.get("required_rulepacks_hint") or []
        hints = [str(x).strip() for x in hints_raw if str(x).strip()]

        return {
            "required": True,
            "min": max(1, min_required),
            "hints": hints,
        }

    def _validate_tool_rulepack_requirements(
        self,
        *,
        tools: Sequence[AgentToolRef],
        rule_pack_ids: Sequence[str],
    ) -> None:
        available_packs = {str(rp).strip() for rp in (rule_pack_ids or []) if str(rp).strip()}
        missing_by_tool: dict[str, dict[str, Any]] = {}

        for t in tools or []:
            key = canonicalize(t.key or "")
            if not key or key not in tool_registry:
                continue

            req = self._get_rulepack_requirement_for_tool(key)
            if not req.get("required"):
                continue

            min_required = int(req.get("min") or 1)
            if len(available_packs) < min_required:
                missing_by_tool[key] = {
                    "min_required": min_required,
                    "available": sorted(available_packs),
                    "hints": req.get("hints") or [],
                }

        if not missing_by_tool:
            return

        parts: list[str] = []
        for tool_key, info in missing_by_tool.items():
            min_req = info["min_required"]
            hints = info.get("hints") or []
            hints_str = f" Suggested packs: {', '.join(hints)}." if hints else ""
            parts.append(f"{tool_key}: requires at least {min_req} rule pack(s).{hints_str}")

        AGENTVERSE_LOGGER.warning(
            LogDomain.EVANGELION_BAY,
            "[🏭 FACTORY][📦 RULES] required_rulepacks_missing",
            extra={
                "missing_rulepack_requirements": missing_by_tool,
            },
        )

        msg = (
            "Agent is missing required rule packs for one or more tools. "
            "Attach rule packs or remove the tools:\n" + "\n".join(f"- {p}" for p in parts)
        )
        raise AgentValidationError(msg, data={"missing_rulepack_requirements": missing_by_tool})

    # ========================== ID & URL GENERATION ==========================

    def _generate_agent_id(self) -> str:
        return str(uuid.uuid4())

    def _generate_chat_url(self, agent_id: str) -> str:
        return f"/chat/{agent_id}"

    def _generate_dna_sequence(self, agent_config: AgentRequest) -> str:
        dna_data = {
            "agent_name": agent_config.name,
            "agent_type": agent_config.type,
            "creator": agent_config.user_id,
        }
        dna_string = json.dumps(dna_data, sort_keys=True)
        return hashlib.sha256(dna_string.encode()).hexdigest()

    def _to_system_name(self, name: str) -> str:
        normalized = unicodedata.normalize("NFKD", name)
        ascii_str = normalized.encode("ascii", "ignore").decode("ascii")
        cleaned = re.sub(r"[^a-zA-Z0-9 ]+", "", ascii_str)
        system_name = cleaned.strip().lower().replace(" ", "_")
        return system_name

    # ========================== BUILD AGENT (DB -> RUNTIME) ==========================

    async def build_agent(
        self,
        socket_emitter,
        request: Request,
        db_agent: Agent,
        user_id: str,
        socket_id: str,
    ) -> BaseAgent:
        AGENTVERSE_LOGGER.info(
            LogDomain.EVANGELION_BAY,
            "[🏭 FACTORY][🚀 BUILD] runtime_build_start",
            extra={
                "agent_id": db_agent.id,
                "agent_name": db_agent.name,
                "agent_type": db_agent.type,
                "user_id": user_id,
                "socket_id": socket_id,
            },
        )

        role_spec = self._resolve_role(db_agent.role_name)

        AGENTVERSE_LOGGER.debug(
            LogDomain.EVANGELION_BAY,
            "[🏭 FACTORY][🧠 ROLE] runtime_role_resolved",
            extra={
                "agent_id": db_agent.id,
                "agent_name": db_agent.name,
                "role_name": role_spec.name,
            },
        )

        self._validate_role_required_tools(role_spec, db_agent.tools)

        agent_class = agent_registry.get(db_agent.type)

        AGENTVERSE_LOGGER.debug(
            LogDomain.EVANGELION_BAY,
            "[🏭 FACTORY][🏭 CLASS] runtime_class_resolved",
            extra={
                "agent_id": db_agent.id,
                "agent_name": db_agent.name,
                "agent_class": getattr(agent_class, "__name__", str(agent_class)),
            },
        )

        resources = self._resolve_runtime_resources(
            request,
            db_agent,
        )

        tool_runtime_dependencies = (
            self._resolve_tool_runtime_dependencies(request)
        )

        agent_params = self._build_agent_params(
            socket_emitter,
            user_id,
            db_agent,
            resources,
        )

        agent_params["rule_pack_ids"] = list(getattr(db_agent, "rule_pack_ids", []) or [])

        workflow_tools = (
            await self.workflow_service.required_tools_for(
                getattr(db_agent, "workflows", None) or []
            )
            if self.workflow_service is not None
            else set()
        )
        enriched_tools = self._enrich_agent_tools(
            db_agent.tools, role_spec, workflow_tools=workflow_tools
        )
        agent_params["tools"] = enriched_tools
        agent_params["role_spec"] = role_spec


        AGENTVERSE_LOGGER.debug(
            LogDomain.EVANGELION_BAY,
            "[🏭 FACTORY][🛠️ TOOLS] runtime_tools_enriched",
            extra={
                "agent_id": db_agent.id,
                "agent_name": db_agent.name,
                "tool_count": len(enriched_tools),
            },
        )

        agent = self._instantiate_agent(agent_class, agent_params, db_agent.name)

        if hasattr(agent, "tool_manager") and agent.tool_manager:
            agent.tool_manager.set_runtime_dependencies(
                tool_runtime_dependencies
            )

        AGENTVERSE_LOGGER.debug(
            LogDomain.EVANGELION_BAY,
            "[🏭 FACTORY][🧰 TOOLS] runtime_dependencies_bound",
            extra={
                "agent_id": db_agent.id,
                "agent_name": db_agent.name,
                "dependencies": sorted(
                    tool_runtime_dependencies.keys()
                ),
            },
        )
        
        agent.bind_runtime(
            request=request,
            default_session_id=socket_id,
            tenant_id=getattr(db_agent, "tenant_id", None),
        )

        try:
            if hasattr(agent, "tool_manager") and agent.tool_manager:
                tool_keys = agent.tool_manager.all_tool_keys()
                AGENTVERSE_LOGGER.debug(
                    LogDomain.EVANGELION_BAY,
                    "[🏭 FACTORY][🛠️ TOOLS] runtime_tools_loaded",
                    extra={
                        "agent_id": db_agent.id,
                        "agent_name": db_agent.name,
                        "tool_keys": tool_keys,
                    },
                )
        except Exception:
            pass

        await agent.mark_spawned(request)

        AGENTVERSE_LOGGER.info(
            LogDomain.EVANGELION_BAY,
            "[🏭 FACTORY][🏁 BUILD] runtime_build_complete",
            extra={
                "agent_id": db_agent.id,
                "agent_name": db_agent.name,
                "role_name": db_agent.role_name,
                "socket_id": socket_id,
            },
        )
        return agent

    # ========================== RUNTIME RESOURCE RESOLUTION ==========================

    def _resolve_runtime_resources(self, request: Request, db_agent: Agent) -> CognitiveResources:
        cf = request.app.state.client_factory

        llm = cf.resolve("llm", db_agent.llm_type, required=True)
        audio = cf.resolve("audio", db_agent.audio_type, required=True)

        image = cf.get_or_noop("image", db_agent.image_type)
        video = cf.get_or_noop("video", db_agent.video_type)
        db = cf.get_or_noop("db", db_agent.db_type)
        cache = cf.get_or_noop("cache", db_agent.cache_type)
        queue_service = request.app.state.queue_service
        vectordb = cf.get_or_noop("knowledge_db", db_agent.knowledge_db_type)
        messaging = cf.get_or_noop("messaging", db_agent.messaging_type)

        container = request.app.container
        rest = container.http.default_http_repository()
        public_settings = container.settings_container.public_agent_settings()
        http_repos = {
            "green_claim_service": container.green_claim.green_claim_service(),
        }

        return CognitiveResources(
            llm=llm,
            audio=audio,
            db=db,
            cache=cache,
            vectordb=vectordb,
            messaging=messaging,
            image=image,
            video=video,
            http=http_repos,
            rest=rest,
            public_settings=public_settings,
            queue_service=queue_service,
        )

    def _resolve_tool_runtime_dependencies(
        self,
        request: Request,
    ) -> dict[str, Any]:
        """
        Resolve shared application services used by tools.
        """
        state = request.app.state
        dependencies: dict[str, Any] = {}

        operation_service_provider = getattr(
            state,
            "operation_service",
            None,
        )
        if operation_service_provider is not None:
            dependencies["operation_service"] = (
                operation_service_provider()
                if callable(operation_service_provider)
                else operation_service_provider
            )

        operation_runner_provider = getattr(
            state,
            "operation_runner",
            None,
        )
        if operation_runner_provider is not None:
            dependencies["operation_runner"] = (
                operation_runner_provider()
                if callable(operation_runner_provider)
                else operation_runner_provider
            )

        job_supervisor = getattr(
            state,
            "job_supervisor",
            None,
        )
        if job_supervisor is not None:
            dependencies["job_supervisor"] = job_supervisor

        dependencies.update(
            self._resolve_registered_tool_runtime_dependencies(
                request,
            )
        )

        return dependencies
    
    def _resolve_registered_tool_runtime_dependencies(
        self,
        request: Request,
    ) -> dict[str, Any]:
        dependencies: dict[str, Any] = {}

        for item in tool_runtime_dependency_registry.items():
            provider = tool_runtime_dependency_registry.get(
                item.key
            )

            dependencies[item.key] = (
                provider(request)
                if callable(provider)
                else provider
            )

        return dependencies

    def _build_agent_params(
        self,
        socket_emitter,
        user_id: str,
        db_agent: Agent,
        resources: CognitiveResources,
    ) -> dict[str, Any]:
        return {
            "id": db_agent.id,
            "user_id": user_id,
            "creator": db_agent.creator,
            "name": db_agent.name,
            "system_name": db_agent.system_name,
            "type": db_agent.type,
            "role_name": db_agent.role_name,
            "rule_pack_ids": list(getattr(db_agent, "rule_pack_ids", []) or []),
            "workflows": list(getattr(db_agent, "workflows", []) or []),
            "prompt": db_agent.prompt,
            "personality_key": db_agent.personality_key,
            "soul": resolve_personality(db_agent.personality_key),
            "llm": resources.llm,
            "audio": resources.audio,
            "image": resources.image,
            "video": resources.video,
            "db": resources.db,
            "cache": resources.cache,
            "vectordb": resources.vectordb,
            "messaging": resources.messaging,
            "http": resources.http,
            "rest": resources.rest,
            "public_settings": resources.public_settings,
            "orchestrator": self.orchestrator,
            "visibility": db_agent.visibility,
            "whitelisted_groups": db_agent.whitelisted_groups or [],
            "collections": db_agent.collections or [],
            "chat_url": db_agent.chat_url,
            "dna_sequence": db_agent.dna_sequence,
            "socket_emitter": socket_emitter,
            "metadata": {},
            "queue_service": resources.queue_service,
        }

    def _instantiate_agent(
        self,
        agent_class,
        agent_params: dict[str, Any],
        agent_name: str,
    ) -> BaseAgent:
        actual_class = getattr(agent_class, "__wrapped__", agent_class)

        is_class = inspect.isclass(actual_class)
        if is_class:
            signature = inspect.signature(actual_class.__init__)
        else:
            signature = inspect.signature(actual_class)

        filtered_params = {
            key: value for key, value in agent_params.items() if key in signature.parameters
        }

        try:
            return actual_class(**filtered_params)
        except Exception:
            AGENTVERSE_LOGGER.exception(
                LogDomain.EVANGELION_BAY,
                "[🏭 FACTORY][💥 FAIL] runtime_instantiation_failed",
                extra={
                    "agent_name": agent_name,
                    "agent_class": getattr(actual_class, "__name__", str(actual_class)),
                    "passed_params": list(filtered_params.keys()),
                },
            )
            raise

    # ========================== TOOL ENRICHMENT ==========================

    def _enrich_agent_tools(
        self,
        agent_tools: list[AgentToolRef] | None,
        role_spec,
        workflow_tools: set[str] | None = None,
    ) -> list[ToolSpec]:
        tools = self._normalize_tools_list(agent_tools)
        tools = self._ensure_core_tools(tools)
        role_defaults = self._get_role_default_tools(role_spec)
        tools = self._merge_tools_by_key(tools, role_defaults)
        role_system = self._get_role_system_tools(role_spec)
        tools = self._merge_tools_by_key(tools, role_system)
        # Folded in last so a tool the agent explicitly equips keeps scope="agent"
        # and its own config even when an attached workflow also requires it.
        tools = self._merge_tools_by_key(
            tools, self.workflow_scoped_tool_specs(workflow_tools or set())
        )
        return tools

    def workflow_scoped_tool_specs(self, workflow_tools: set[str]) -> list[ToolSpec]:
        """Specs for tools an attached workflow needs but the agent did not equip.

        These are runnable by the workflow engine and deliberately absent from the
        direct-invocation surface, so attaching a workflow never widens the chat
        surface. Registry defaults apply - equipping a tool is how you tune it.
        """
        return [self._tool_spec_for(key, {}, scope="workflow") for key in sorted(workflow_tools)]

    def _tool_spec_for(
        self, key: str, config_overrides: dict[str, Any], *, scope: ToolScope = "agent"
    ) -> ToolSpec:
        """Expand a tool key into a full ToolSpec from its registry metadata.

        The single place tool metadata is read: registry defaults are the base and
        ``config_overrides`` wins on conflicts. Unknown keys yield an empty metadata
        dict rather than raising, so a stale role declaration degrades to a bare spec
        instead of breaking agent startup - but it is logged, not swallowed.
        """
        if key not in tool_registry:
            AGENTVERSE_LOGGER.warning(
                LogDomain.EVANGELION_BAY,
                "[🏭 FACTORY][🛠️ TOOLS] tool_key_unregistered",
                extra={"tool_key": key},
            )

        meta = tool_registry.get_metadata_for_ref(key)
        info = tool_registry.get_info(key)

        return ToolSpec(
            key=key,
            name=(
                meta.get("display_name")
                or meta.get("remote_tool_name")
                or (info.name if info else None)
                or key
            ),
            version=meta.get("version"),
            scope=scope,
            config={**(meta.get("default_config") or {}), **config_overrides},
        )

    def _ref_to_spec(self, ref: AgentToolRef) -> ToolSpec:
        """Expand an AgentToolRef into a full ToolSpec using the registry + agent config."""
        return self._tool_spec_for(ref.key, ref.config or {})

    def _normalize_tools_list(self, tools: list[AgentToolRef] | None) -> list[ToolSpec]:
        return [self._ref_to_spec(ref) for ref in tools or []]

    def _ensure_core_tools(self, tools: list[ToolSpec]) -> list[ToolSpec]:
        """Append the core tools every agent gets, unless it already declares them."""
        existing_keys = {t.key for t in tools}
        tools.extend(
            self._tool_spec_for(key, {}) for key in CORE_TOOL_KEYS if key not in existing_keys
        )
        return tools

    def _get_role_default_tools(self, role_spec) -> list[ToolSpec]:
        caps = getattr(role_spec, "capabilities", {}) or {}
        overrides = caps.get("tool_overrides") or {}
        return [
            self._tool_spec_for(key, dict(overrides.get(key) or {}))
            for key in getattr(role_spec, "default_tools", []) or []
        ]

    def _get_role_system_tools(self, role_spec) -> list[ToolSpec]:
        caps = getattr(role_spec, "capabilities", {}) or {}
        bindings: dict[str, str] = caps.get("system_tools") or {}
        return [
            self._tool_spec_for(
                key,
                {
                    "_hidden": True,
                    "_required": True,
                    "_logical": logical_name,
                },
                scope="system",
            )
            for logical_name, key in bindings.items()
        ]

    def _merge_tools_by_key(self, base: list[ToolSpec], extra: list[ToolSpec]) -> list[ToolSpec]:
        """Fold ``extra`` into ``base`` by canonical key. ``base`` wins on conflicts;
        ``extra`` only contributes tools base lacks and fills gaps on ones it shares.
        """
        by_key: dict[str, ToolSpec] = {t.key: t for t in base}

        for tool in extra:
            existing = by_key.get(tool.key)
            if existing is None:
                by_key[tool.key] = tool
                continue

            existing.config = {**(tool.config or {}), **(existing.config or {})}
            existing.name = existing.name or tool.name
            existing.version = existing.version or tool.version

        return list(by_key.values())
