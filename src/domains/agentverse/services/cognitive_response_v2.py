"""One governed conversational inference; no V1 runtime or tool orchestration."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from typing import Any

from src.base.events.v2.contracts import EventPermissionError
from src.base.infrastructure.ai.llm_usage import collect_llm_usage
from src.domains.agentverse.agents.conversational_composition_v2 import supports_v2_conversation
from src.domains.agentverse.agents.mixins.prompt import PromptMixin
from src.domains.agentverse.entities.cognitive_v2 import (
    AgentCognitiveProfile,
    CognitiveContext,
    CognitiveResult,
    CognitiveTurn,
)
from src.domains.agentverse.ncsc.cpbx import AuthorizedSynapticClient
from src.domains.agentverse.ncsc.models import Synapse
from src.domains.agentverse.personalities.utils.default_personality import DEFAULT_PERSONALITY_KEY
from src.domains.agentverse.personalities.utils.resolve_personality import resolve_personality
from src.domains.agentverse.registries import (
    personality_registry_instance,
    role_registry_instance,
    rulepack_registry_instance,
)
from src.domains.agentverse.security.revocation import ExecutionPermit
from src.domains.agentverse.security.utu import UTUAuthority
from src.domains.agentverse.services.conversation_history_v2 import ConversationHistoryV2

SIGNAL = "chat.response"


class CognitiveResponseUnavailable(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class CognitiveResponseV2:
    def __init__(
        self,
        *,
        definitions: Any,
        authority: UTUAuthority,
        history: ConversationHistoryV2,
        client_factory: Callable[[], Any],
    ) -> None:
        self.definitions = definitions
        self.authority = authority
        self.history = history
        self.client_factory = client_factory

    async def handle(self, synapse: Synapse, client: AuthorizedSynapticClient) -> dict[str, Any]:
        # Identity and session come exclusively from the CPBX-issued permit.
        permit = client.execution_permit
        if not isinstance(permit, ExecutionPermit) or (
            permit.key.tenant_id,
            permit.key.agent_id,
        ) != (synapse.tenant_id, synapse.source):
            raise EventPermissionError("Agent-scoped response permit required")
        principal = await self.authority.resolve_agent_principal(permit.key)
        session = await self.authority.execution_session(principal)
        if SIGNAL not in self.authority.grants_for(principal).invoke:
            raise EventPermissionError("LLM invocation grant required")
        payload = synapse.payload
        if set(payload) != {"conversation_id", "text"}:
            raise ValueError("Invalid response payload")
        turn = CognitiveTurn(
            id=synapse.correlation_id,
            tenant_id=principal.tenant_id,
            conversation_id=payload["conversation_id"],
            user_id=session.user_id,
            agent_id=principal.agent_id,
            message=payload["text"],
            correlation_id=synapse.correlation_id,
            causation_id=synapse.causation_id or synapse.correlation_id,
        )
        history = await self.history.project(
            session, turn.conversation_id, turn.agent_id, exclude_event_id=turn.id
        )
        definition = await self.definitions.find_by_id(turn.agent_id)
        if not definition or definition.get("runtime_version") != "v2":
            raise EventPermissionError("Explicit V2 definition required")
        if (definition.get("id"), definition.get("creator")) != (turn.agent_id, turn.user_id) or (
            definition.get("tenant_id", turn.tenant_id) != turn.tenant_id
        ):
            raise EventPermissionError("Definition scope mismatch")
        if not supports_v2_conversation(definition.get("type"), definition.get("role_name")):
            raise CognitiveResponseUnavailable("composition_unavailable")
        try:
            role_key = role_registry_instance.resolve_key(
                definition.get("role_name") or "general", kind="role"
            )
            role = role_registry_instance.get_role(role_key)
            if definition.get("type") == "form" and (role.agent_group, role.agent_subclass) != (
                "interactive_ui",
                "operational",
            ):
                raise ValueError("Conversational Form requires its approved operational role")
            personality = definition.get("personality_key") or DEFAULT_PERSONALITY_KEY
            personality_registry_instance.get(
                personality
            )  # explicit invalid references fail closed
            soul = resolve_personality(personality)
            packs = tuple(definition.get("rule_pack_ids") or ())
            rules = [r.description for r in (role.rules or [])]
            for pack in packs:
                rules.extend(r.description for r in rulepack_registry_instance.get(pack).rules)
            if (role.capabilities or {}).get("required_tools"):
                raise ValueError("This milestone does not execute role tools")
        except (KeyError, LookupError, ValueError):
            raise CognitiveResponseUnavailable("composition_unavailable") from None
        factory = self.client_factory()
        try:
            if factory is None:
                raise ValueError("Runtime catalog unavailable")
            preferred = definition.get("llm_type")
            # Historical trusted Genesis seed stored a provider label, not a model.
            # Its resource is the running catalog's explicit default; never guess a slug.
            if definition.get("genesis_local_seed") is True and preferred == "openai":
                preferred = factory.defaults.get("llm")
                if not preferred:
                    raise ValueError("Runtime default unavailable")
            key = factory.resolve_key("llm", preferred, required=True)
            repo = factory.get_repo("llm", key)
            if (
                repo is None
                or getattr(repo, "is_noop", False)
                or not callable(getattr(repo, "get_response_async", None))
            ):
                raise ValueError("Real asynchronous model required")
        except (ValueError, KeyError, AttributeError):
            raise CognitiveResponseUnavailable("llm_provider_unavailable") from None
        profile = AgentCognitiveProfile(
            agent_id=turn.agent_id,
            definition_version=hashlib.sha256(
                json.dumps(definition, sort_keys=True, default=str).encode()
            ).hexdigest(),
            type=definition["type"],
            role=role_key,
            personality=personality,
            rule_packs=packs,
            capabilities=(SIGNAL,),
            cognitive_resources=(key,),
        )
        context = CognitiveContext(
            profile=profile,
            turn=turn,
            history=history,
            principal=principal,
            authorized_capabilities=(SIGNAL,),
        )
        instructions = "\n\n".join(
            [
                PromptMixin().build_system_prompt_from_soul(soul)["content"],
                f"Agent type: {profile.type}. Role: {role.name}. {role.description or role.summary or ''}",
                "Rules:\n" + "\n".join(rules),
                str(definition.get("prompt") or ""),
                (
                    f"Your conversational identity is {definition.get('name') or turn.agent_id}. "
                    "Reply directly to the user. No tools or external actions are available."
                ),
            ]
        )
        messages = [*context.history.chat_messages(), {"role": "user", "content": turn.message}]
        usage: list[dict[str, Any]] = []
        try:
            with collect_llm_usage(usage):
                content = await repo.get_response_async(
                    input=messages, instructions=instructions, temperature=None
                )
        except Exception:  # noqa: BLE001 - provider errors must not expose credentials
            # Never serialize provider exception bodies, headers or credentials.
            raise CognitiveResponseUnavailable("llm_provider_failed") from None
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise asyncio.CancelledError
        if not isinstance(content, str) or not content.strip() or len(content) > 65536:
            raise CognitiveResponseUnavailable("llm_response_invalid")
        await self.authority.check_permit(principal.permit)
        await self.history.authorize(session, turn.conversation_id, (turn.agent_id,))
        result = CognitiveResult(
            turn_id=turn.id, agent_id=turn.agent_id, kind="agent_response", content=content
        )
        backend = getattr(repo, "client", None)
        return {
            "agent_id": turn.agent_id,
            "response": result.model_dump(mode="json"),
            "llm": {
                "key": key,
                "provider": usage[-1].get("provider")
                if usage
                else getattr(backend, "provider", None),
                "model": usage[-1].get("model")
                if usage
                else getattr(backend, "chat_deployment", None),
            },
        }
