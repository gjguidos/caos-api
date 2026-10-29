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
    sessions = RedisAgentSessionAuthority(
        repository=_RedisBinding(container.redis.redis_repository),
        jwt_service=container.services.jwt_service(),
    )
    return {
        "genesis_security": GenesisSecurityBindings(
            sessions=sessions,
            approvals=approvals,
            authorization=SynapticAuthorization(
                grants=grants,
                trusted_lines=[TrustedLine(**item) for item in config.get("trusted_lines", [])],
            ),
        )
    }


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
        if os.getenv("GENESIS_LOCAL_ENABLED") == "true":
            from src.domains.agentverse.dependencies.genesis_local import initialize_local

            await initialize_local(app.container)
        await sessions.start()

    app.state.start_genesis_sessions = start
    app.state.close_genesis_sessions = sessions.close
