"""Opt-in application composition. Configuration is trusted, never request data."""

from __future__ import annotations

import inspect
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI

if TYPE_CHECKING:
    from src.base.dependencies.di_container import Container

from src.domains.agentverse.agents.lifecycle.agent_principal_resolver import AgentActivationApproval
from src.domains.agentverse.dependencies.genesis_security import GenesisSecurityBindings
from src.domains.agentverse.ncsc.authorization import SynapticAuthorization
from src.domains.agentverse.ncsc.models import SynapticGrants, TrustedLine
from src.domains.agentverse.security.sessions import RedisAgentSessionAuthority
from src.domains.agentverse.tools.store_tool.models import ProvisionedIdentity


class _RedisBinding:
    """Resolve the existing DI repository after its async resource is ready."""

    def __init__(self, provider: Callable[[], Any]) -> None:
        self._provider = provider

    async def eval(self, script: str, keys: list[str], args: list[Any]) -> Any:
        repository = self._provider()
        if inspect.isawaitable(repository):
            repository = await repository
        return await repository.eval(script, keys=keys, args=args)


def build_genesis_security(container: Container) -> dict[str, GenesisSecurityBindings]:
    if os.environ.get("GENESIS_SESSIONS_ENABLED", "false").lower() != "true":
        return {}
    # An optional server-owned JSON file supplies a deployment policy snapshot.
    # Loading does not provision tenant state in Redis.
    path = os.environ.get("GENESIS_SECURITY_POLICY_FILE")
    config = json.loads(Path(path).read_text()) if path else {}
    approvals = {}
    for item in config.get("approvals", []):
        approval = AgentActivationApproval(**item)
        if type(approval.enabled) is not bool:
            raise ValueError("Genesis approval enabled must be boolean")
        scope = (approval.tenant_id, approval.agent_id)
        if scope in approvals:
            raise ValueError("Duplicate Genesis approval")
        approvals[scope] = approval
    grants = {}
    for item in config.get("grants", []):
        scope = (item["tenant_id"], item["agent_id"])
        if scope in grants or any(not isinstance(v, str) or not v for v in scope):
            raise ValueError("Invalid or duplicate Genesis grant scope")
        invoke = item["invoke"]
        if not isinstance(invoke, list) or any(not isinstance(v, str) or not v for v in invoke):
            raise ValueError("Genesis invoke grants must be a list of exact signals")
        grants[scope] = SynapticGrants(invoke)
    provisions = _provisions(config.get("provisioned", []), approvals)
    sessions = RedisAgentSessionAuthority(
        repository=_RedisBinding(container.redis.redis_repository),
        jwt_service=container.services.jwt_service(),
    )
    return {
        "genesis_security": GenesisSecurityBindings(
            sessions=sessions,
            approvals=approvals,
            provisions=provisions,
            authorization=SynapticAuthorization(
                grants=grants,
                trusted_lines=[TrustedLine(**item) for item in config.get("trusted_lines", [])],
            ),
        )
    }


def _provisions(
    items: Any, approvals: dict[tuple[str, str], AgentActivationApproval]
) -> dict[str, ProvisionedIdentity]:
    """Strict policy-pinned identities. Each authorizes one principal for one agent ID."""
    if not isinstance(items, list):
        raise TypeError("Genesis provisioned identities must be a list")
    provisions: dict[str, ProvisionedIdentity] = {}
    agent_ids: set[tuple[str, str]] = set()
    for item in items:
        entry = ProvisionedIdentity.model_validate(item)
        scope = (entry.tenant_id, entry.agent_id)
        if entry.slot in provisions or scope in agent_ids:
            raise ValueError("Duplicate Genesis provisioned slot or agent ID")
        if entry.agent_id == entry.principal_id:
            raise ValueError("A provisioned identity cannot be its own provisioner")
        approval = approvals.get(scope)
        # A pre-configured activation approval must belong to the same owner the
        # store will record as creator, or the agent could never be activated.
        if approval is not None and approval.owner_user_id != entry.subject_id:
            raise ValueError("Provisioned subject does not match the activation approval owner")
        provisions[entry.slot] = entry
        agent_ids.add(scope)
    return provisions


def configure_genesis_transport(app: FastAPI, bindings: GenesisSecurityBindings) -> None:
    # Other application-supplied adapters retain their established transport.
    if not isinstance(bindings.sessions, RedisAgentSessionAuthority):
        return
    sessions = bindings.sessions
    app.state.genesis_sessions = sessions

    async def start() -> None:
        from src.domains.agentverse.registries import synaptic_registry_instance
        from src.domains.agentverse.services.cognitive_response_v2 import (
            SIGNAL,
            CognitiveResponseV2,
        )

        av = app.container.agentverse
        av.utu_authority()
        if any(
            "chat.response" in grant.invoke for grant in bindings.authorization._grants.values()
        ):
            responder = CognitiveResponseV2(
                definitions=av.db_service(),
                authority=av.utu_authority(),
                history=av.client_event_dispatcher_v2().history,
                client_factory=lambda: getattr(app.state, "client_factory", None),
            )
            synaptic_registry_instance.subscribe(SIGNAL, provider="cognitive.chat")(
                responder.handle
            )
        from src.domains.agentverse.tools.store_tool import register_store_create
        from src.domains.agentverse.tools.store_tool.spec import STORE_CREATE_SIGNAL

        policy = bindings.authorization
        if any(STORE_CREATE_SIGNAL in grant.invoke for grant in policy._grants.values()) or any(
            line.target == STORE_CREATE_SIGNAL for line in policy._trusted_lines
        ):
            register_store_create(synaptic_registry_instance, av.store().tool())
        from src.domains.agentverse.tools.store_tool import (
            ProvisionedStoreCapability,
            register_store_create_provisioned,
        )
        from src.domains.agentverse.tools.store_tool.spec import STORE_CREATE_PROVISIONED_SIGNAL

        if bindings.provisions and (
            any(
                STORE_CREATE_PROVISIONED_SIGNAL in grant.invoke for grant in policy._grants.values()
            )
            or any(line.target == STORE_CREATE_PROVISIONED_SIGNAL for line in policy._trusted_lines)
        ):
            register_store_create_provisioned(
                synaptic_registry_instance,
                ProvisionedStoreCapability(tool=av.store().tool(), provisions=bindings.provisions),
            )
        from src.domains.agentverse.tools.validation_tool import register_data_validation
        from src.domains.agentverse.tools.validation_tool.spec import DATA_VALIDATION_SIGNAL

        if any(DATA_VALIDATION_SIGNAL in grant.invoke for grant in policy._grants.values()) or any(
            line.target == DATA_VALIDATION_SIGNAL for line in policy._trusted_lines
        ):
            register_data_validation(synaptic_registry_instance, av.validation().tool())
        if os.getenv("GENESIS_LOCAL_ENABLED") == "true":
            from src.domains.agentverse.dependencies.genesis_local import initialize_local

            await initialize_local(app.container)
        await sessions.start()

    app.state.start_genesis_sessions = start
    app.state.close_genesis_sessions = sessions.close
