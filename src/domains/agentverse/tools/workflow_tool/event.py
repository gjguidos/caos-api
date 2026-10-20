from __future__ import annotations

from typing import Any

from src.domains.agentverse.events.socket_event_emitter import SocketEventEmitter
from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.tools.workflow_tool.public_contract import step_event


def make_on_event(*, socket_emitter: SocketEventEmitter | None):
    async def _on_event(event: dict[str, Any]) -> None:
        if event.get("type") != "workflow.step":
            AGENTVERSE_LOGGER.debug(
                LogDomain.TOOL_BAY,
                "[WORKFLOW] dropping non-public workflow event",
                extra={"event_type": event.get("type")},
            )
            return

        if not socket_emitter:
            AGENTVERSE_LOGGER.warning(
                LogDomain.TOOL_BAY,
                "[WORKFLOW] missing socket emitter for workflow event",
                extra={
                    "run_id": event.get("run_id"),
                    "step_id": event.get("step_id"),
                },
            )
            return

        outgoing = step_event(event)
        if outgoing is None:
            return

        await socket_emitter.emit("workflow.step", outgoing)

    return _on_event


def make_run_snapshot_emitter(*, socket_emitter: SocketEventEmitter | None):
    async def _emit(snapshot: dict[str, Any]) -> None:
        if not socket_emitter:
            return
        try:
            await socket_emitter.emit("workflow.run", snapshot)
        except Exception:
            AGENTVERSE_LOGGER.warning(
                LogDomain.TOOL_BAY,
                "[WORKFLOW] failed to emit workflow.run snapshot",
                extra={"run_id": snapshot.get("run_id"), "status": snapshot.get("status")},
            )

    return _emit
