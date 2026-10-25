"""Runtime selection is persisted metadata, independent of agent type/providers."""

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
