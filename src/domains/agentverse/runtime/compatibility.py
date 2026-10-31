"""Runtime selection is persisted metadata, independent of agent type/providers."""

from collections.abc import Mapping
from typing import Any

from src.domains.agentverse.entities.agent import Agent
from src.domains.agentverse.exceptions import AgentverseDomainException


class UnsupportedAgentRuntimeError(AgentverseDomainException):
    def __init__(self) -> None:
        super().__init__(
            message="This V2 agent cannot run in V1 chat. Open /v2/conversations with an authorized session and participant.",
            status_code=409,
            error_code="UNSUPPORTED_AGENT_RUNTIME",
        )


def require_v1_runtime(agent: Agent) -> None:
    if agent.runtime_version != "v1":
        raise UnsupportedAgentRuntimeError()


# --- AGENT-DEF-SEC-0.1: governed V2 definition integrity ------------------------------------


class GovernedDefinitionError(AgentverseDomainException):
    """A legacy (V1) path tried to change a governed V2 definition.

    Raised only after the caller's existing read and write checks pass, so it reveals
    nothing to a requester who could not already see the Agent. Stable code, no Agent data.
    """

    def __init__(self) -> None:
        super().__init__(
            message=(
                "This Agent's definition is governed. It cannot be changed or deleted through "
                "legacy Agent routes."
            ),
            status_code=409,
            error_code="AGENT_DEFINITION_GOVERNED",
        )


def is_governed_definition(definition: Mapping[str, Any] | None) -> bool:
    """A definition created by a governed path: V2 runtime, a Store creation record or a
    trusted local seed. Fails closed for any runtime other than V1. These markers are
    server-managed and no legacy route can set or remove them."""
    if not isinstance(definition, Mapping):
        return False
    return (
        definition.get("runtime_version") not in (None, "v1")
        or definition.get("store") is not None
        or definition.get("genesis_local_seed") is True
    )


def require_legacy_mutable(definition: Mapping[str, Any] | None) -> None:
    """Legacy mutation (V1 update, delete, collection link) applies to V1 definitions only."""
    if is_governed_definition(definition):
        raise GovernedDefinitionError()
