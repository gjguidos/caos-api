from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

# -----------------------------------------------------------------------------
# Domain taxonomy
# -----------------------------------------------------------------------------


class LogDomain(str, Enum):
    COMMAND_ROOM = "command_room"
    SECURITY_BAY = "security_bay"
    OPERATIONS_COMMANDER = "operations_commander"
    INTERNAL_INTELLIGENCE = "internal_intelligence"
    EVANGELION_BAY = "evangelion_bay"
    NERV_HQ = "nerv_hq"
    EXISTENTIAL_INDEX = "existential_index"
    AGENT_EXISTENTIAL_CORE = "agent_existential_core"
    TOOL_BAY = "tool_bay"
    UNIVERSAL_ORCHESTRATOR = "universal_orchestrator"
    COGNITION_BAY = "cognition_bay"
    EVA_UNIT = "eva_unit"
    SOCKET_BRIDGE = "socket_bridge"


DOMAIN_LABELS: dict[LogDomain, str] = {
    LogDomain.COMMAND_ROOM: "🧭 COMMAND ROOM",
    LogDomain.SECURITY_BAY: "🚓 SECURITY BAY",
    LogDomain.OPERATIONS_COMMANDER: "🧠 OPERATIONS COMMANDER",
    LogDomain.EVANGELION_BAY: "🧬 EVANGELION BAY",
    LogDomain.NERV_HQ: "🧠 NERV HQ",
    LogDomain.EXISTENTIAL_INDEX: "🧠 EXISTENTIAL INDEX",
    LogDomain.AGENT_EXISTENTIAL_CORE: "🧠 AGENT EXISTENTIAL CORE",
    LogDomain.TOOL_BAY: "🧰 TOOL BAY",
    LogDomain.UNIVERSAL_ORCHESTRATOR: "🧭 UNIVERSAL ORCHESTRATOR",
    LogDomain.COGNITION_BAY: "🧠 COGNITION BAY",
    LogDomain.EVA_UNIT: "🤖 EVA UNIT",
    LogDomain.SOCKET_BRIDGE: "📡 SOCKET BRIDGE",
    LogDomain.INTERNAL_INTELLIGENCE: "🧠 INTERNAL INTELLIGENCE",
}


# -----------------------------------------------------------------------------
# Logger service
# -----------------------------------------------------------------------------


@dataclass(slots=True)
class AgentVerseLogger:
    logger: logging.Logger

    def log(
        self,
        level: int,
        domain: LogDomain,
        message: str,
        *,
        exc_info: bool = False,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.logger.log(
            level,
            message,
            extra={
                "domain": domain.value,
                "domain_label": DOMAIN_LABELS[domain],
                "extra_fields": extra or {},
            },
            exc_info=exc_info,
        )

    def debug(
        self,
        domain: LogDomain,
        message: str,
        *,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.log(logging.DEBUG, domain, message, extra=extra)

    def info(
        self,
        domain: LogDomain,
        message: str,
        *,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.log(logging.INFO, domain, message, extra=extra)

    def warning(
        self,
        domain: LogDomain,
        message: str,
        *,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.log(logging.WARNING, domain, message, extra=extra)

    def error(
        self,
        domain: LogDomain,
        message: str,
        *,
        exc_info: bool = False,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.log(logging.ERROR, domain, message, exc_info=exc_info, extra=extra)

    def exception(
        self,
        domain: LogDomain,
        message: str,
        *,
        extra: dict[str, Any] | None = None,
    ) -> None:
        self.log(logging.ERROR, domain, message, exc_info=True, extra=extra)


# -----------------------------------------------------------------------------
# Public singleton
# -----------------------------------------------------------------------------

AGENTVERSE_LOGGER_NAME = "agentverse"

AGENTVERSE_LOGGER = AgentVerseLogger(logger=logging.getLogger(AGENTVERSE_LOGGER_NAME))


# -----------------------------------------------------------------------------
# Backward-compatible helper functions
# -----------------------------------------------------------------------------


def log_command_room(message: str, *, extra: dict[str, Any] | None = None) -> None:
    AGENTVERSE_LOGGER.info(LogDomain.COMMAND_ROOM, message, extra=extra)


def log_operations_commander(message: str, *, extra: dict[str, Any] | None = None) -> None:
    AGENTVERSE_LOGGER.info(LogDomain.OPERATIONS_COMMANDER, message, extra=extra)


def log_evangelion_bay(message: str, *, extra: dict[str, Any] | None = None) -> None:
    AGENTVERSE_LOGGER.info(LogDomain.EVANGELION_BAY, message, extra=extra)


def log_nerv_hq(message: str, *, extra: dict[str, Any] | None = None) -> None:
    AGENTVERSE_LOGGER.info(LogDomain.NERV_HQ, message, extra=extra)


def log_existential_index(message: str, *, extra: dict[str, Any] | None = None) -> None:
    AGENTVERSE_LOGGER.info(LogDomain.EXISTENTIAL_INDEX, message, extra=extra)


def log_agent_existential_core(message: str, *, extra: dict[str, Any] | None = None) -> None:
    AGENTVERSE_LOGGER.info(LogDomain.AGENT_EXISTENTIAL_CORE, message, extra=extra)


def log_tool_bay(message: str, *, extra: dict[str, Any] | None = None) -> None:
    AGENTVERSE_LOGGER.info(LogDomain.TOOL_BAY, message, extra=extra)


def log_universal_orchestrator(message: str, *, extra: dict[str, Any] | None = None) -> None:
    AGENTVERSE_LOGGER.info(LogDomain.UNIVERSAL_ORCHESTRATOR, message, extra=extra)


def log_cognition_bay(message: str, *, extra: dict[str, Any] | None = None) -> None:
    AGENTVERSE_LOGGER.info(LogDomain.COGNITION_BAY, message, extra=extra)


def log_eva_unit(message: str, *, extra: dict[str, Any] | None = None) -> None:
    AGENTVERSE_LOGGER.info(LogDomain.EVA_UNIT, message, extra=extra)


def log_socket_bridge(message: str, *, extra: dict[str, Any] | None = None) -> None:
    AGENTVERSE_LOGGER.info(LogDomain.SOCKET_BRIDGE, message, extra=extra)

def log_internal_intelligence(message: str, *, extra: dict[str, Any] | None = None) -> None:
    AGENTVERSE_LOGGER.info(LogDomain.INTERNAL_INTELLIGENCE, message, extra=extra)
