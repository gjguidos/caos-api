"""
Simplified AgentService for CRUD operations on agents.

This service provides a clean interface for creating, reading, updating,
deleting, and listing agents. It orchestrates between the database layer,
validation logic, and agent factory.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request

from src.base.entities.user import User
from src.domains.agentverse.agents.base import BaseAgent
from src.domains.agentverse.agents.factory import AgentFactory
from src.domains.agentverse.entities.agent import Agent
from src.domains.agentverse.entities.agent_request import AgentRequest
from src.domains.agentverse.entities.agent_update_request import AgentUpdateRequest
from src.domains.agentverse.entities.report_log import OperationType
from src.domains.agentverse.entities.workflow import slugify_workflow_key
from src.domains.agentverse.events.socket_contract import LogLevel, LogStage
from src.domains.agentverse.exceptions import (
    AgentAccessDeniedError,
    AgentNotFoundError,
    AgentValidationError,
    InvalidComponentError,
)
from src.domains.agentverse.logging.logger_v1 import AGENTVERSE_LOGGER, LogDomain
from src.domains.agentverse.registries import agent_registry_instance as agent_registry
from src.domains.agentverse.registries import personality_registry_instance as personality_registry
from src.domains.agentverse.runtime.compatibility import require_v1_runtime
from src.domains.agentverse.security import agent_access
from src.domains.agentverse.services.audit_service import AuditService
from src.domains.agentverse.services.db_service import DBService
from src.domains.agentverse.services.workflow_service import WorkflowService
from src.domains.agentverse.tools.tool_manager import ToolManager


class AgentService:
    """
    Service for agent CRUD operations.

    This service handles:
    - Creating new agents with validation
    - Retrieving agents by ID
    - Listing all agents
    - Updating existing agents
    - Deleting agents
    """

    COMPONENT_MAPPING = {
        "llm_type": "llm",
        "audio_type": "audio",
        "image_type": "image",
        "video_type": "video",
        "embedding_type": "embedding",
        "db_type": "db",
        "cache_type": "cache",
        "knowledge_db_type": "knowledge_db",
        "messaging_type": "messaging",
    }

    def __init__(
        self,
        agent_factory: AgentFactory,
        db_service: DBService,
        audit_service: AuditService,
        workflow_service: WorkflowService,
    ):
        self.agent_factory = agent_factory
        self.db_service = db_service
        self.audit_service = audit_service
        self.workflow_service = workflow_service

    # ========================== CREATE ==========================

    async def create_agent(
        self,
        socket_emitter,
        request: Request,
        agent_config: AgentRequest,
        user: User,
    ) -> Agent:
        AGENTVERSE_LOGGER.info(
            LogDomain.OPERATIONS_COMMANDER,
            "[🧬 AGENT][🏗️ CREATE] create_start",
            extra={
                "agent_name": agent_config.name,
                "creator_user_id": user.id,
            },
        )
        self._validate_whitelisted_groups(user, agent_config.whitelisted_groups)

        AGENTVERSE_LOGGER.debug(
            LogDomain.OPERATIONS_COMMANDER,
            "[🧬 AGENT][👯 CHECK] duplicate_scan_start",
            extra={
                "agent_name": agent_config.name,
                "creator_user_id": user.id,
            },
        )

        await self.db_service.check_for_duplicates(socket_emitter, agent_config)

        AGENTVERSE_LOGGER.debug(
            LogDomain.OPERATIONS_COMMANDER,
            "[🧬 AGENT][👯 CHECK] duplicate_scan_clear",
            extra={
                "agent_name": agent_config.name,
                "creator_user_id": user.id,
            },
        )

        AGENTVERSE_LOGGER.debug(
            LogDomain.OPERATIONS_COMMANDER,
            "[🧬 AGENT][🧠 VALIDATE] config_prepare_start",
            extra={
                "agent_name": agent_config.name,
                "creator_user_id": user.id,
            },
        )

        await self._validate_and_prepare_config(socket_emitter, request, agent_config)

        AGENTVERSE_LOGGER.debug(
            LogDomain.OPERATIONS_COMMANDER,
            "[🧬 AGENT][🧠 VALIDATE] config_prepare_complete",
            extra={
                "agent_name": agent_config.name,
                "creator_user_id": user.id,
            },
        )

        AGENTVERSE_LOGGER.info(
            LogDomain.OPERATIONS_COMMANDER,
            "[🧬 AGENT][🏭 BUILD] entity_create_start",
            extra={
                "agent_name": agent_config.name,
                "creator_user_id": user.id,
            },
        )

        agent = await self.agent_factory.create_agent(socket_emitter, agent_config)

        AGENTVERSE_LOGGER.info(
            LogDomain.OPERATIONS_COMMANDER,
            "[🧬 AGENT][🏭 BUILD] entity_create_complete",
            extra={
                "agent_id": agent.id,
                "agent_name": agent.name,
                "creator_user_id": agent.creator,
            },
        )

        AGENTVERSE_LOGGER.info(
            LogDomain.OPERATIONS_COMMANDER,
            "[🧬 AGENT][💾 INDEX] persist_start",
            extra={
                "agent_id": agent.id,
                "agent_name": agent.name,
            },
        )

        stored_agent = await self.db_service.store_agent(socket_emitter, agent)

        AGENTVERSE_LOGGER.info(
            LogDomain.OPERATIONS_COMMANDER,
            "[🧬 AGENT][💾 INDEX] persist_complete",
            extra={
                "agent_id": agent.id,
                "agent_name": agent.name,
            },
        )

        await self.try_log_operation(
            operation_type=OperationType.AGENT_CREATE,
            user_object_id=agent.creator,
            payload={
                "agent_id": agent.id,
                "stored_agent": stored_agent,
            },
        )

        AGENTVERSE_LOGGER.info(
            LogDomain.OPERATIONS_COMMANDER,
            "[🧬 AGENT][🏁 CREATE] create_complete",
            extra={
                "agent_id": agent.id,
                "agent_name": agent.name,
                "creator_user_id": agent.creator,
            },
        )

        return Agent(**stored_agent)

    # ========================== READ ==========================

    async def get_agent(
        self,
        agent_id: str,
        user: User,
    ) -> Agent:
        AGENTVERSE_LOGGER.info(
            LogDomain.OPERATIONS_COMMANDER,
            "[🧬 AGENT][📖 READ] fetch_start",
            extra={
                "agent_id": agent_id,
                "request_user_id": user.id,
            },
        )

        agent_data = await self.db_service.find_by_id(agent_id)

        if not agent_data:
            AGENTVERSE_LOGGER.warning(
                LogDomain.OPERATIONS_COMMANDER,
                "[🧬 AGENT][📖 READ] fetch_missing",
                extra={
                    "agent_id": agent_id,
                    "request_user_id": user.id,
                },
            )
            raise AgentNotFoundError(agent_id)

        agent = Agent(**agent_data)

        if not agent_access.can_read(user, agent):
            AGENTVERSE_LOGGER.warning(
                LogDomain.OPERATIONS_COMMANDER,
                "[🧬 AGENT][🔐 ACCESS] read_denied",
                extra={
                    "agent_id": agent_id,
                    "request_user_id": user.id,
                },
            )
            raise AgentAccessDeniedError(
                user_id=user.id,
                agent_id=agent_id,
                action="read",
                reason="user lacks read permission",
            )

        AGENTVERSE_LOGGER.info(
            LogDomain.OPERATIONS_COMMANDER,
            "[🧬 AGENT][📖 READ] fetch_complete",
            extra={
                "agent_id": agent.id,
                "agent_name": agent.name,
                "request_user_id": user.id,
            },
        )

        return agent

    async def list_agents(self, user: User) -> list[Agent]:
        AGENTVERSE_LOGGER.info(
            LogDomain.OPERATIONS_COMMANDER,
            "[🧬 AGENT][📚 LIST] list_start",
            extra={
                "request_user_id": user.id,
            },
        )

        read_filter = agent_access.build_read_filter(user)
        agents_data = await self.db_service.find_all(query=read_filter)
        agents = [Agent(**data) for data in agents_data]

        AGENTVERSE_LOGGER.info(
            LogDomain.OPERATIONS_COMMANDER,
            "[🧬 AGENT][📚 LIST] list_complete",
            extra={
                "request_user_id": user.id,
                "count": len(agents),
            },
        )
        return agents

    # ========================== UPDATE ==========================

    async def update_agent(
        self,
        request: Request,
        agent_id: str,
        update_request: AgentUpdateRequest,
        user: User,
    ) -> Agent:
        AGENTVERSE_LOGGER.info(
            LogDomain.OPERATIONS_COMMANDER,
            "[🧬 AGENT][🔄 UPDATE] update_start",
            extra={
                "agent_id": agent_id,
                "request_user_id": user.id,
            },
        )

        existing = await self.db_service.find_by_id(agent_id)
        if not existing:
            AGENTVERSE_LOGGER.warning(
                LogDomain.OPERATIONS_COMMANDER,
                "[🧬 AGENT][🔄 UPDATE] update_missing",
                extra={
                    "agent_id": agent_id,
                    "request_user_id": user.id,
                },
            )
            raise AgentNotFoundError(agent_id)

        existing_agent = Agent(**existing)

        if not agent_access.can_write(user, existing_agent):
            AGENTVERSE_LOGGER.warning(
                LogDomain.OPERATIONS_COMMANDER,
                "[🧬 AGENT][🔐 ACCESS] write_denied",
                extra={
                    "agent_id": agent_id,
                    "request_user_id": user.id,
                    "action": "update",
                },
            )
            raise AgentAccessDeniedError(
                user_id=user.id,
                agent_id=agent_id,
                action="update",
                reason="user lacks write permission",
            )

        # Step 3: Validate that the user belongs to any groups they are trying to whitelist
        try:
            if update_request.whitelisted_groups is not None:
                self._validate_whitelisted_groups(user, update_request.whitelisted_groups)

            existing_agent = Agent(**existing)

            # Attached workflows are validated against the *effective* post-update
            # config, so removing a rule pack an attached workflow needs is caught
            # here rather than mid-run.
            if update_request.workflows is not None:
                update_request.workflows = self._normalize_workflow_keys(update_request.workflows)
            effective_workflows = (
                update_request.workflows
                if update_request.workflows is not None
                else list(existing_agent.workflows or [])
            )
            effective_rule_pack_ids = (
                update_request.rule_pack_ids
                if update_request.rule_pack_ids is not None
                else list(existing_agent.rule_pack_ids or [])
            )
            workflow_required_kinds = await self._validate_workflow_requirements(
                workflow_keys=effective_workflows,
                rule_pack_ids=effective_rule_pack_ids,
            )

            # Step 4: Validate the update request (includes role, tools, rule packs, components)
            extra_fields = self._validate_update_request(
                request, update_request, existing_agent, workflow_required_kinds
            )
        except ValueError as e:
            raise AgentValidationError(message=str(e))

        update_data = update_request.model_dump_for_update()

        # Merge in any extra fields produced by validation (e.g. resolved rules, system_name)
        update_data.update(extra_fields)

        if not update_data:
            AGENTVERSE_LOGGER.info(
                LogDomain.OPERATIONS_COMMANDER,
                "[🧬 AGENT][🔄 UPDATE] update_noop",
                extra={
                    "agent_id": agent_id,
                    "request_user_id": user.id,
                },
            )
            return existing_agent

        updated_data = await self.db_service.update_agent(agent_id, update_data)

        if not updated_data:
            AGENTVERSE_LOGGER.error(
                LogDomain.OPERATIONS_COMMANDER,
                "[🧬 AGENT][💥 FAIL] update_returned_empty",
                extra={
                    "agent_id": agent_id,
                    "request_user_id": user.id,
                },
            )
            raise AgentNotFoundError(agent_id)

        agent = Agent(**updated_data)

        # Step 7: Invalidate cached instances so next access re-spawns from DB
        try:
            from src.domains.agentverse.cache import helpers as av_cache

            await av_cache.invalidate_agent(request, agent_id)
            AGENTVERSE_LOGGER.info(
                LogDomain.OPERATIONS_COMMANDER,
                f"[🧬 AGENT][🔄 UPDATE] Invalidated cached instances for agent: {agent_id}",
                extra={
                    "agent_id": agent_id,
                    "request_user_id": user.id,
                },
            )
        except Exception as e:
            AGENTVERSE_LOGGER.warning(
                LogDomain.OPERATIONS_COMMANDER,
                f"[🧬 AGENT][🔄 UPDATE] Failed to invalidate cache for agent {agent_id}: {e}",
                extra={
                    "agent_id": agent_id,
                    "request_user_id": user.id,
                },
            )

        # Step 8: Log the update operation
        AGENTVERSE_LOGGER.info(
            LogDomain.OPERATIONS_COMMANDER,
            f"[🧬 AGENT][🔄 UPDATE] Logging the update of EVA unit '{agent.name}' in the audit service...",
            extra={
                "agent_id": agent_id,
                "request_user_id": user.id,
            },
        )

        await self.try_log_operation(
            operation_type=OperationType.AGENT_UPDATE,
            user_object_id=user.id,
            payload={
                "agent_id": agent.id,
                "agent_name": agent.name,
                "changes": update_data,
                "updated_fields": list(update_data.keys()),
            },
        )

        AGENTVERSE_LOGGER.info(
            LogDomain.OPERATIONS_COMMANDER,
            "[🧬 AGENT][🔄 UPDATE] update_complete",
            extra={
                "agent_id": agent.id,
                "agent_name": agent.name,
                "request_user_id": user.id,
                "updated_fields": list(update_data.keys()),
            },
        )

        return agent

    # ========================== DELETE ==========================

    async def delete_agent(self, agent_id: str, user: User) -> bool:
        AGENTVERSE_LOGGER.info(
            LogDomain.OPERATIONS_COMMANDER,
            "[🧬 AGENT][🗑️ DELETE] delete_start",
            extra={
                "agent_id": agent_id,
                "request_user_id": user.id,
            },
        )

        agent_data = await self.db_service.find_by_id(agent_id)
        if not agent_data:
            AGENTVERSE_LOGGER.warning(
                LogDomain.OPERATIONS_COMMANDER,
                "[🧬 AGENT][🗑️ DELETE] delete_missing",
                extra={
                    "agent_id": agent_id,
                    "request_user_id": user.id,
                },
            )
            raise AgentNotFoundError(agent_id)

        agent = Agent(**agent_data)

        if not agent_access.can_write(user, agent):
            AGENTVERSE_LOGGER.warning(
                LogDomain.OPERATIONS_COMMANDER,
                "[🧬 AGENT][🔐 ACCESS] delete_denied",
                extra={
                    "agent_id": agent_id,
                    "request_user_id": user.id,
                    "action": "delete",
                },
            )
            raise AgentAccessDeniedError(
                user_id=user.id,
                agent_id=agent_id,
                action="delete",
                reason="user lacks write permission",
            )

        success = await self.db_service.delete_chat_agent(agent_id)

        if success:
            await self.try_log_operation(
                operation_type=OperationType.AGENT_DELETE,
                user_object_id=user.id,
                payload={
                    "agent_id": agent_id,
                    "agent_name": agent_data.get("name", "unknown"),
                    "agent_type": agent_data.get("type", "unknown"),
                },
            )

            AGENTVERSE_LOGGER.info(
                LogDomain.OPERATIONS_COMMANDER,
                "[🧬 AGENT][🗑️ DELETE] delete_complete",
                extra={
                    "agent_id": agent_id,
                    "agent_name": agent_data.get("name"),
                    "request_user_id": user.id,
                },
            )
        else:
            AGENTVERSE_LOGGER.warning(
                LogDomain.OPERATIONS_COMMANDER,
                "[🧬 AGENT][🗑️ DELETE] delete_returned_false",
                extra={
                    "agent_id": agent_id,
                    "request_user_id": user.id,
                },
            )

        return success

    async def get_runtime_agent(
        self,
        socket_emitter,
        request: Request,
        agent_id: str,
        user: User,
        socket_id: str,
    ) -> BaseAgent:
        db_agent = await self.get_agent(agent_id, user)
        require_v1_runtime(db_agent)

        AGENTVERSE_LOGGER.info(
            LogDomain.OPERATIONS_COMMANDER,
            "[🧬 AGENT][🚀 RUNTIME] runtime_build_start",
            extra={
                "agent_id": db_agent.id,
                "agent_name": db_agent.name,
                "request_user_id": user.id,
                "socket_id": socket_id,
            },
        )

        agent = await self.agent_factory.build_agent(
            socket_emitter,
            request,
            db_agent,
            user.id,
            socket_id,
        )

        if not agent:
            AGENTVERSE_LOGGER.error(
                LogDomain.OPERATIONS_COMMANDER,
                "[🧬 AGENT][💥 FAIL] runtime_build_failed",
                extra={
                    "agent_id": db_agent.id,
                    "agent_name": db_agent.name,
                    "request_user_id": user.id,
                    "socket_id": socket_id,
                },
            )
            raise RuntimeError("Agent construction failed - factory returned None.")

        AGENTVERSE_LOGGER.info(
            LogDomain.OPERATIONS_COMMANDER,
            "[🧬 AGENT][🚀 RUNTIME] runtime_build_complete",
            extra={
                "agent_id": agent.id,
                "agent_name": agent.name,
                "request_user_id": user.id,
                "socket_id": socket_id,
            },
        )
        return agent

    # ========================== VALIDATION HELPERS ==========================

    async def _validate_and_prepare_config(
        self,
        socket_emitter,
        request: Request,
        agent_config: AgentRequest,
    ) -> set | None:
        await socket_emitter.log(
            "Validating and preparing EVA configuration...",
            level=LogLevel.INFO,
            stage=LogStage.VALIDATE,
        )
        required_kinds = None

        requested_type, requested_personality = agent_config.type, agent_config.personality_key
        agent_config.type = self._resolve_agent_type_key(requested_type)
        agent_config.personality_key = self._resolve_personality_key(requested_personality)

        if (agent_config.type, agent_config.personality_key) != (
            requested_type,
            requested_personality,
        ):
            AGENTVERSE_LOGGER.debug(
                LogDomain.OPERATIONS_COMMANDER,
                "[🧬 AGENT][🧠 VALIDATE] keys_normalized",
                extra={
                    "requested_type": requested_type,
                    "resolved_type": agent_config.type,
                    "requested_personality": requested_personality,
                    "resolved_personality": agent_config.personality_key,
                },
            )

        if agent_config.tools:
            normalized_specs, tool_required_kinds = ToolManager.validate_specs(agent_config.tools)
            agent_config.tools = normalized_specs
            required_kinds = tool_required_kinds

            AGENTVERSE_LOGGER.debug(
                LogDomain.OPERATIONS_COMMANDER,
                "[🧬 AGENT][🧠 VALIDATE] tool_specs_normalized",
                extra={
                    "tool_count": len(normalized_specs),
                    "required_kinds": sorted(required_kinds),
                },
            )

        agent_config.workflows = self._normalize_workflow_keys(agent_config.workflows)
        workflow_kinds = await self._validate_workflow_requirements(
            workflow_keys=agent_config.workflows,
            rule_pack_ids=agent_config.rule_pack_ids or [],
        )
        if workflow_kinds:
            required_kinds = (required_kinds or set()) | workflow_kinds

        if required_kinds:
            await self._fill_missing_backends(
                socket_emitter,
                request,
                agent_config,
                required_kinds,
            )

        await self._validate_component_types_helper(
            socket_emitter,
            request,
            agent_config,
            required_kinds,
        )

        return required_kinds

    @staticmethod
    def _normalize_workflow_keys(keys: list[str] | None) -> list[str]:
        """Canonicalize attached workflow keys to the slug form the catalog stores.

        Every read path slugifies (``WorkflowService.get_workflow``,
        ``authorize_workflow_key``), so a raw key like ``"My Workflow"`` still
        resolves at run time but is invisible to the exact-match query behind the
        delete-safety check — the row would be deletable out from under the agent
        holding it. Deduplicated because two spellings can collapse to one slug.
        """
        normalized: dict[str, None] = {}
        for key in keys or []:
            slug = slugify_workflow_key(key)
            if slug:
                normalized[slug] = None
        return list(normalized)

    async def _validate_workflow_requirements(
        self,
        *,
        workflow_keys: list[str] | None,
        rule_pack_ids: list[str],
    ) -> set[str]:
        """Attached workflows must exist and be satisfiable by this agent's config.

        Every key must name a catalog row: a typo belongs in an admin-visible save
        failure, not in a user-visible failure the day someone clicks Run. This is
        the moment that rejects a missing key; at run time ``required_tools_for``
        skips one instead, so a since-deleted workflow cannot brick the others.

        Beyond existence, tool keys are auto-equipped at workflow scope, so only
        requirements that can't be scope-hidden are checked: the backend kinds the
        required tools declare, and rule-pack minimums. Returns the required
        backend kinds so the caller can fold them into the set it fills in.
        """
        if not workflow_keys:
            return set()

        workflows, missing = await self.workflow_service.get_workflows(workflow_keys)
        if missing:
            raise AgentValidationError(
                message=(
                    f"Unknown workflow key(s): {', '.join(missing)}. "
                    "Attach only workflows that exist in the catalog."
                ),
                data={"missing_workflow_keys": missing},
            )

        required_tools = self.workflow_service.required_tools_of(workflows)
        if not required_tools:
            return set()

        specs = self.agent_factory.workflow_scoped_tool_specs(required_tools)
        _, required_kinds = ToolManager.validate_specs(specs)
        self.agent_factory._validate_tool_rulepack_requirements(
            tools=specs,
            rule_pack_ids=rule_pack_ids,
        )

        AGENTVERSE_LOGGER.debug(
            LogDomain.OPERATIONS_COMMANDER,
            "[🧬 AGENT][👷 WORKFLOW] workflow_requirements_validated",
            extra={
                "workflow_keys": list(workflow_keys),
                "workflow_tool_keys": sorted(required_tools),
                "required_kinds": sorted(required_kinds),
            },
        )
        return required_kinds

    def _validate_update_request(
        self,
        request: Request,
        update_request: AgentUpdateRequest,
        existing_agent: Agent,
        workflow_required_kinds: set[str] | None = None,
    ) -> dict[str, Any]:
        """
        Validate an agent update request against both the update payload and
        the existing agent state to enforce the same invariants as creation.

        Returns:
            Dict of extra fields to include in the DB update (e.g. resolved rules,
            system_name). These are merged into the update payload by the caller.

        Raises:
            InvalidComponentError: If component validation fails
            ValueError: If tool/role/rule-pack validation fails
        """
        extra_update_fields: dict[str, Any] = {}

        # --- 0. Validate agent type if it is being changed ---
        if update_request.type is not None:
            extra_update_fields["type"] = self._resolve_agent_type_key(update_request.type)

        # --- 1. Validate & normalize tool specs if provided ---
        required_kinds = self._validate_update_tools(update_request)
        if workflow_required_kinds:
            required_kinds = (required_kinds or set()) | workflow_required_kinds

        # --- 2. Validate component types if any backend fields changed ---
        self._validate_update_components(request, update_request, required_kinds)

        # --- 3. Validate role and resolve rules if role_name changed ---
        role_spec = self._validate_update_role(update_request, existing_agent, extra_update_fields)

        # --- 4. Cross-field validation: tools vs role vs rule packs ---
        effective_tools = (
            update_request.tools if update_request.tools is not None else existing_agent.tools
        )
        effective_rule_pack_ids = (
            update_request.rule_pack_ids
            if update_request.rule_pack_ids is not None
            else list(existing_agent.rule_pack_ids or [])
        )
        self._validate_update_cross_fields(
            update_request, role_spec, effective_tools, effective_rule_pack_ids
        )

        # --- 5. Validate & normalize personality key if provided ---
        if update_request.personality_key is not None:
            extra_update_fields["personality_key"] = self._resolve_personality_key(
                update_request.personality_key
            )

        # --- 6. Regenerate system_name if name is changing ---
        if update_request.name is not None:
            extra_update_fields["system_name"] = self.agent_factory._to_system_name(
                update_request.name
            )

        return extra_update_fields

    def _resolve_agent_type_key(self, agent_type: str) -> str:
        """Resolve an agent type to the canonical registry key it addresses, so the
        persisted ``type`` always matches the key ``AgentFactory`` later resolves by.
        """
        return agent_registry.resolve_key(agent_type, kind="agent type")

    def _resolve_personality_key(self, personality_key: str) -> str:
        """Resolve a personality reference to the canonical registry key it addresses."""
        return personality_registry.resolve_key(personality_key, kind="personality")

    def _validate_update_tools(self, update_request: AgentUpdateRequest) -> set | None:
        """Validate and normalize tool specs on the update request."""
        if update_request.tools is None:
            return None
        normalized_specs, tool_required_kinds = ToolManager.validate_specs(update_request.tools)
        update_request.tools = normalized_specs
        return tool_required_kinds

    def _validate_update_components(
        self, request: Request, update_request: AgentUpdateRequest, required_kinds: set | None
    ) -> None:
        """Validate backend component types on the update request."""
        has_component_updates = any(
            getattr(update_request, field, None) is not None for field in self.COMPONENT_MAPPING
        )
        if not has_component_updates and not required_kinds:
            return
        if required_kinds:
            self._fill_missing_backends_sync(request, update_request, required_kinds)
        self._validate_component_types_sync(request, update_request, required_kinds)

    def _validate_update_role(
        self,
        update_request: AgentUpdateRequest,
        existing_agent: Agent,
        extra_update_fields: dict[str, Any],
    ):
        """Validate role_name and re-resolve rules if the role is changing."""
        effective_role_name = (
            update_request.role_name
            if update_request.role_name is not None
            else existing_agent.role_name
        )
        if not effective_role_name:
            return None

        role_spec = self.agent_factory._resolve_role(effective_role_name)

        return role_spec

    def _validate_update_cross_fields(
        self,
        update_request: AgentUpdateRequest,
        role_spec,
        effective_tools,
        effective_rule_pack_ids: list[str],
    ) -> None:
        """Validate tools against role requirements and rule pack requirements."""
        if update_request.tools is not None:
            self.agent_factory._validate_tools(update_request.tools)

        if role_spec and effective_tools:
            self.agent_factory._validate_role_required_tools(role_spec, effective_tools)

        if effective_tools:
            self.agent_factory._validate_tool_rulepack_requirements(
                tools=effective_tools,
                rule_pack_ids=effective_rule_pack_ids,
            )

    def _fill_missing_backends_sync(
        self, request: Request, config: Any, required_kinds: set
    ) -> None:
        """Synchronous version of _fill_missing_backends for update validation."""
        defaults = getattr(request.app.state, "cognitive_defaults", {}) or {}
        for field, kind in self.COMPONENT_MAPPING.items():
            current_value = getattr(config, field, None)
            if current_value in (None, "", "null"):
                setattr(config, field, defaults.get(kind) or "")

    def _validate_component_types_sync(
        self, request: Request, config: Any, required_kinds: set | None = None
    ) -> None:
        """Synchronous version of _validate_component_types_helper for update validation."""
        modules = request.app.state.cognitive_modules
        errors = []
        required_kinds_set = required_kinds or set()
        for field, kind in self.COMPONENT_MAPPING.items():
            value = getattr(config, field, None)
            is_required = kind in required_kinds_set
            if is_required and not value:
                errors.append((field, value))
                continue
            if value and value not in modules.get(kind, {}):
                errors.append((field, value))
        if errors:
            raise InvalidComponentError(errors)

    async def _fill_missing_backends(
        self,
        socket_emitter,
        request: Request,
        config: Any,
        required_kinds: set,
    ) -> None:
        AGENTVERSE_LOGGER.debug(
            LogDomain.OPERATIONS_COMMANDER,
            "[🧬 AGENT][🧠 VALIDATE] fill_missing_backends",
            extra={"required_kinds": sorted(required_kinds)},
        )

        defaults = getattr(request.app.state, "cognitive_defaults", {}) or {}

        for field, kind in self.COMPONENT_MAPPING.items():
            current_value = getattr(config, field, None)
            if current_value in (None, "", "null"):
                setattr(config, field, defaults.get(kind) or "")

    async def _validate_component_types_helper(
        self,
        socket_emitter,
        request: Request,
        config: Any,
        required_kinds: set | None = None,
    ) -> None:
        modules = request.app.state.cognitive_modules
        errors = []
        required_kinds_set = required_kinds or set()

        for field, kind in self.COMPONENT_MAPPING.items():
            value = getattr(config, field, None)
            is_required = kind in required_kinds_set

            if is_required and not value:
                errors.append((field, value))
                continue

            if value and value not in modules.get(kind, {}):
                errors.append((field, value))

        if errors:
            AGENTVERSE_LOGGER.warning(
                LogDomain.OPERATIONS_COMMANDER,
                "[🧬 AGENT][🧠 VALIDATE] component_validation_failed",
                extra={
                    "errors": errors,
                },
            )
            raise InvalidComponentError(errors)

        AGENTVERSE_LOGGER.debug(
            LogDomain.OPERATIONS_COMMANDER,
            "[🧬 AGENT][✅ VALIDATE] component_validation_success",
        )

    def _validate_whitelisted_groups(self, user: User, whitelisted_groups: list[str]) -> None:
        if not whitelisted_groups:
            return

        user_groups = agent_access.get_user_groups(user)
        requested = set(whitelisted_groups)

        if not requested.issubset(user_groups):
            disallowed = requested - user_groups

            AGENTVERSE_LOGGER.warning(
                LogDomain.OPERATIONS_COMMANDER,
                "[🧬 AGENT][🔐 ACCESS] whitelist_validation_failed",
                extra={
                    "request_user_id": user.id,
                    "requested_groups": sorted(requested),
                    "user_groups": sorted(user_groups),
                    "disallowed_groups": sorted(disallowed),
                },
            )

            raise ValueError(
                f"User {user.id} cannot whitelist groups they don't belong to: {disallowed}"
            )

    # ========================== OTHER HELPERS ==========================

    async def try_log_operation(
        self,
        operation_type: OperationType,
        user_object_id: str,
        payload: dict[str, Any],
    ) -> None:
        try:
            await self.audit_service.log_operation(
                operation_type=operation_type,
                user_object_id=user_object_id,
                payload=payload,
            )
        except Exception:
            AGENTVERSE_LOGGER.exception(
                LogDomain.OPERATIONS_COMMANDER,
                "[🧬 AGENT][💥 FAIL] audit_log_failed",
                extra={
                    "audit_operation_type": operation_type.value,
                    "user_object_id": user_object_id,
                },
            )
