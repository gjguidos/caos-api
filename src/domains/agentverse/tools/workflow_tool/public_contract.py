from __future__ import annotations

from typing import Any

from src.domains.agentverse.tools.workflow_tool.formatter import present_output_display

OUTPUT_STATUSES = {"COMPLETED", "FAILED", "CANCELLED"}


def output_display(step_id: str, value: Any) -> dict[str, Any]:
    """Serialize a raw workflow output into the public display-only shape.

    Workflow outputs are stored in the canonical ``{"tool": ..., "env": ...}``
    shape (see ``libs/engine/execute/tool.py``), which is what both the run
    snapshot and the per-step event carry. We project the env once here.
    """
    if isinstance(value, dict) and "env" in value:
        tool_name = str(value.get("tool") or step_id)
        return present_output_display(tool_name, value["env"])

    # Defensive: anything outside the canonical shape is presented as-is.
    return present_output_display(step_id, value)


def step_event(event: dict[str, Any]) -> dict[str, Any] | None:
    """Serialize an internal workflow.step event for the socket contract."""
    if event.get("type") != "workflow.step":
        return None

    step_id = str(event.get("step_id") or "")
    payload = event.get("payload")
    status = _resolve_status(event, payload)

    public: dict[str, Any] = {
        "kind": "workflow.step",
        "run_id": event.get("run_id"),
        "workflow_key": event.get("workflow_key"),
        "step_id": step_id,
        "status": status,
    }

    if status in OUTPUT_STATUSES and payload is not None:
        public["output"] = output_display(step_id, payload)

    return public


def _resolve_status(event: dict[str, Any], payload: Any) -> str:
    status = event.get("status")
    if isinstance(status, str) and status.strip():
        return status.upper()

    if isinstance(payload, dict):
        payload_status = payload.get("status")
        if isinstance(payload_status, str) and payload_status.strip():
            return payload_status.upper()

        env = payload.get("env")
        if isinstance(env, dict):
            env_status = env.get("status")
            if isinstance(env_status, str) and env_status.strip():
                return env_status.upper()
            return "COMPLETED" if bool(env.get("success", env.get("ok", True))) else "FAILED"

    return "RUNNING"
