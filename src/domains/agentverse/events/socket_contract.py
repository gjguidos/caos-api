"""Declared contract for the structured log and agent-creation socket traffic.

This module is the single source of truth for the vocabulary the backend and
frontend agree on for two specific wire shapes; traffic that references these
types should reference them here so the contract is greppable, type-checked,
and cannot silently drift:

- Per-line ``log`` frames (``LogLevel``, ``LogStage``), emitted via
  ``SocketEventEmitter.log``.
- The one-shot terminal ``joshu-a.create`` event envelope (``CreateStatus``,
  ``AgentRef``, ``CreateResult``).

It does not cover the other frame types the emitter can send (``chat``,
``event``, ``error``); those are not yet modelled here.

Two rules for the log frames covered above:

- Only send messages that the user watching the UI can act on. Internal
  reasoning and per-execution chatter belongs in the container logs
  (``AGENTVERSE_LOGGER``), not on the wire.
- Every log line declares its ``level`` (and, on lifecycle milestones, its
  ``stage``). The backend never infers these from the message prose.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class LogLevel(StrEnum):
    """Severity of a streamed log line. Drives client styling."""

    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


class LogStage(StrEnum):
    """Macro-stage of the agent-creation lifecycle. Drives the progress stepper."""

    PARSE = "parse"
    VALIDATE = "validate"
    CONFIGURE = "configure"
    REGISTER = "register"
    TEST = "test"
    FINALIZE = "finalize"


class CreateStatus(StrEnum):
    """Terminal outcome of a ``joshu-a.create`` attempt.

    Wire values are intentionally uppercase to match the established frontend
    contract (``response.status === "OK"`` means success).
    """

    OK = "OK"
    ERROR = "ERROR"


@dataclass(frozen=True)
class AgentRef:
    """The subset of an agent the client needs to reference a created agent."""

    agent_id: str
    agent_system_name: str


@dataclass(frozen=True)
class CreateResult:
    """The terminal ``joshu-a.create`` envelope, shared by success and failure.

    Both outcomes serialize to the same shape; only field values differ. This is
    the single serializer for the terminal event so the two paths cannot drift.
    """

    status: CreateStatus
    response: str
    agent: AgentRef | None = None

    @classmethod
    def ok(cls, *, agent_id: str, agent_system_name: str, response: str) -> CreateResult:
        return cls(
            status=CreateStatus.OK,
            response=response,
            agent=AgentRef(agent_id=agent_id, agent_system_name=agent_system_name),
        )

    @classmethod
    def error(cls, response: str) -> CreateResult:
        return cls(status=CreateStatus.ERROR, response=response, agent=None)

    def to_payload(self) -> dict:
        return {
            "status": self.status,
            "agent": (
                {
                    "agent_id": self.agent.agent_id,
                    "agent_system_name": self.agent.agent_system_name,
                }
                if self.agent is not None
                else None
            ),
            "response": self.response,
        }
