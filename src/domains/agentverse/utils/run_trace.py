"""The per-turn tool telemetry sink.

``execute_router`` is the single funnel every tool execution passes through, so
it is the only place that has to record anything. It appends one step record
per call to a plain list carried on the orchestration context under
``TRACE_KEY``; the report builder reads that list back at the end of the turn.

Step records are deliberately generic. ``children`` lets a composite tool (the
workflow tool, today) report its own sub-steps without ``report.py`` growing any
knowledge of what a workflow is, and nested workflows nest naturally.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from src.domains.agentverse.utils.redaction import preview_text, summarize_value

TRACE_KEY = "_tool_trace"

STATUS_OK = "ok"
STATUS_ERROR = "error"
STATUS_VETOED = "vetoed"
STATUS_BLOCKED = "blocked"


def utc_now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def resolve_sink(*sources: Any) -> list[dict[str, Any]] | None:
    """Find the trace sink in the first source that carries one.

    The sink is threaded through the same dicts ``_socket_emitter`` already
    travels in, so callers hand over whichever mappings they have in scope.
    """
    for source in sources:
        if isinstance(source, dict):
            sink = source.get(TRACE_KEY)
            if isinstance(sink, list):
                return sink
    return None


def summarize_result(payload: Any) -> dict[str, Any]:
    """Reduce a tool envelope to counts, kinds, a text preview and an error."""
    summary: dict[str, Any] = {}
    if not isinstance(payload, dict):
        preview = preview_text(payload if isinstance(payload, str) else None)
        if preview:
            summary["text_preview"] = preview
        return summary

    if "success" in payload:
        summary["success"] = bool(payload.get("success"))
    if payload.get("error"):
        summary["error"] = preview_text(str(payload["error"]))

    data = payload.get("result")
    if not isinstance(data, dict):
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload

    if isinstance(data, dict):
        if data.get("status"):
            summary["status"] = summarize_value(data["status"])
        if data.get("kind"):
            summary["kind"] = summarize_value(data["kind"])
        for key in ("images", "sources", "documents", "results"):
            value = data.get(key)
            if isinstance(value, list):
                summary[f"{key}_count"] = len(value)
        for key in ("answer", "text", "content", "message", "summary"):
            preview = preview_text(data.get(key))
            if preview:
                summary["text_preview"] = preview
                break

    return summary


def extract_children(payload: Any) -> list[dict[str, Any]]:
    """Lift a composite tool's sub-steps out of its envelope.

    A tool opts in by putting a list of step-shaped dicts on
    ``meta.addons.steps``; everything else returns an empty list.
    """
    if not isinstance(payload, dict):
        return []
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        result = payload.get("result")
        meta = result.get("meta") if isinstance(result, dict) else None
    if not isinstance(meta, dict):
        return []
    addons = meta.get("addons")
    if not isinstance(addons, dict):
        return []
    steps = addons.get("steps")
    if not isinstance(steps, list):
        return []
    return [summarize_value(step) for step in steps if isinstance(step, dict)]


def record_step(
    sink: list[dict[str, Any]] | None,
    *,
    tool: str,
    status: str,
    started_at: str,
    latency_ms: int,
    route: str | None = None,
    args: dict[str, Any] | None = None,
    result: Any = None,
    error: str | None = None,
) -> dict[str, Any] | None:
    """Append one step record to the sink. Never raises into the caller.

    ``args`` is expected to be already summarized (``summarize_args``): the
    caller has to capture them before the tool runs, so it owns that step.
    """
    if sink is None:
        return None
    try:
        step = {
            "index": len(sink),
            "tool": tool,
            "route": route,
            "phase": None,
            "status": status,
            "started_at": started_at,
            "ended_at": utc_now_iso(),
            "latency_ms": latency_ms,
            "args": args or {},
            "result": summarize_result(result),
            "error": preview_text(error) if error else None,
            "children": extract_children(result),
        }
        sink.append(step)
        return step
    except Exception:
        return None
