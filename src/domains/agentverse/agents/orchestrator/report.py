"""Run report v2 - the observability record for one orchestrated turn.

Every field here traces back to something an actual writer populates. v1
promised a rich record and delivered mostly nulls because it read keys nothing
wrote (``token_usage``, ``rapport_trace`` tool calls) and reached for
``getattr`` on plain dicts; v2 reads only real signals: the tool trace
``execute_router`` fills, the LLM side-channel the client feeds, and the
context bags ``universal.py`` already lifts out of tool addons.

Two contracts matter to callers:

- **It always returns a valid report.** Each block is built independently and a
  block that raises records ``{field, error}`` in ``report["errors"]`` and
  leaves its field null. Building a report must never cost the user their turn.
- **Payloads are summarized, never embedded.** Arguments and results arrive
  pre-redacted from the trace; anything added here goes through the same
  helpers so no credential or base64 blob reaches Mongo.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from src.domains.agentverse.utils.redaction import preview_text, summarize_value
from src.domains.agentverse.utils.run_trace import TRACE_KEY

REPORT_VERSION = "2.0"


def build_run_report(ctx: dict[str, Any] | None) -> dict[str, Any]:
    """Build the v2 report for a finished orchestration context."""
    ctx = ctx if isinstance(ctx, dict) else {}
    errors: list[dict[str, str]] = []

    report: dict[str, Any] = {"version": REPORT_VERSION}
    for field, builder in _BLOCKS:
        report[field] = _safe(field, builder, ctx, errors)
    report["errors"] = errors
    return report


def error_run_report(error: str) -> dict[str, Any]:
    """A v2-shaped report for when the builder itself could not run.

    Every block is null, exactly as a single failing block leaves its field, so
    a consumer reading ``report["steps"]`` finds the key either way and the
    reason sits in ``errors`` where it already looks for one.
    """
    report: dict[str, Any] = {"version": REPORT_VERSION}
    for field, _ in _BLOCKS:
        report[field] = None
    report["errors"] = [{"field": "report", "error": error}]
    return report


def _safe(
    field: str,
    builder: Callable[[dict[str, Any]], Any],
    ctx: dict[str, Any],
    errors: list[dict[str, str]],
) -> Any:
    try:
        return builder(ctx)
    except Exception as e:
        errors.append({"field": field, "error": f"{type(e).__name__}: {e}"})
        return None


# --------------------------------------------------------------------------
# Blocks
# --------------------------------------------------------------------------


def _run_block(ctx: dict[str, Any]) -> dict[str, Any]:
    started_at = ctx.get("started_at")
    ended_at = ctx.get("ended_at")
    return {
        "run_id": ctx.get("run_id"),
        "session_id": ctx.get("session_id"),
        "agent_id": ctx.get("_agent_id"),
        "role": ctx.get("role"),
        "started_at": started_at,
        "ended_at": ended_at,
        # Wall clock, which is what a reader of the run block wants. The sum of
        # phase timings is a different number and lives in `timings.total_ms`.
        "duration_ms": _elapsed_ms(started_at, ended_at),
    }


def _elapsed_ms(started_at: Any, ended_at: Any) -> int | None:
    if not isinstance(started_at, str) or not isinstance(ended_at, str):
        return None
    try:
        delta = datetime.fromisoformat(ended_at) - datetime.fromisoformat(started_at)
    except ValueError:
        return None
    return int(delta.total_seconds() * 1000)


def _input_block(ctx: dict[str, Any]) -> dict[str, Any]:
    return {
        "request": preview_text(ctx.get("request")),
        "raw_request": preview_text(ctx.get("raw_request")),
        "goal": preview_text(ctx.get("goal")),
        "history_turns": len(ctx.get("history") or []),
    }


def _output_block(ctx: dict[str, Any]) -> dict[str, Any]:
    artifact = ctx.get("artifact") or {}
    content = artifact.get("content")

    # Images can ride along under a text artifact, so count them wherever they
    # are rather than gating on artifact["kind"] the way v1 did.
    images = artifact.get("images")
    if not isinstance(images, list):
        images = content if artifact.get("kind") == "images" and isinstance(content, list) else []

    return {
        "kind": artifact.get("kind") or "text",
        "chars": len(content) if isinstance(content, str) else None,
        "preview": preview_text(content),
        "images": {
            "count": len(images) or None,
            "sizes": [i.get("size") for i in images if isinstance(i, dict)] or None,
        },
        "sources": summarize_value(ctx.get("sources")) if ctx.get("sources") else None,
        "assumptions": summarize_value(ctx.get("assumptions")) if ctx.get("assumptions") else None,
        "missing": summarize_value(ctx.get("missing")) if ctx.get("missing") else None,
    }


def _routing_block(ctx: dict[str, Any]) -> dict[str, Any]:
    brief = ctx.get("brief") or {}
    plan = ctx.get("plan") or {}
    return {
        "intent": brief.get("intent") if isinstance(brief, dict) else None,
        "acceptance": brief.get("acceptance") if isinstance(brief, dict) else None,
        "selected_tool": ctx.get("selected_tool"),
        "confidence": float(ctx.get("route_confidence") or 0.0),
        # v1 carried this same list three times over as actions/plan_steps/planned.
        "plan_steps": summarize_value(plan.get("steps") or ctx.get("actions_needed") or []),
        "signals": summarize_value(ctx.get("signals")) if ctx.get("signals") else None,
    }


def _steps_block(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    trace = ctx.get(TRACE_KEY)
    return [step for step in trace if isinstance(step, dict)] if isinstance(trace, list) else []


def _llm_block(ctx: dict[str, Any]) -> list[dict[str, Any]]:
    calls = ctx.get("_llm_calls")
    return [call for call in calls if isinstance(call, dict)] if isinstance(calls, list) else []


def _evaluation_block(ctx: dict[str, Any]) -> dict[str, Any] | None:
    evaluation = ctx.get("eval")
    return summarize_value(evaluation) if isinstance(evaluation, dict) else None


def _tool_reports_block(ctx: dict[str, Any]) -> dict[str, Any]:
    """Every sub-report tools produced, including the auditor's.

    ``universal.py`` already lifts ``addons.reports`` into ``ctx["reports"]``,
    so the auditor no longer needs its own envelope sibling on the reply.
    """
    reports = ctx.get("reports")
    if not isinstance(reports, dict):
        return {}
    return {name: summarize_value(value) for name, value in reports.items()}


def _cost_block(ctx: dict[str, Any]) -> dict[str, Any]:
    """Roll up the wallet events tools already emit.

    Tools price themselves in integer minor units via ``BaseTool.pricing``, so
    the report only has to sum what they reported. LLM calls are listed with
    their token counts but left unpriced - a hardcoded price table here would
    be wrong within a quarter.
    """
    by_tool: dict[str, int] = {}
    currency: str | None = None
    total = 0

    for entry in ctx.get("addons_log") or []:
        if not isinstance(entry, dict):
            continue
        addons = entry.get("addons")
        if not isinstance(addons, dict):
            continue
        tool = str(entry.get("tool") or "unknown")
        for event in addons.get("wallet_events") or []:
            if not isinstance(event, dict):
                continue
            amount = event.get("amount_minor")
            if not isinstance(amount, int):
                continue
            currency = currency or event.get("currency")
            by_tool[tool] = by_tool.get(tool, 0) + amount
            total += amount

    return {
        "currency": currency or "USD",
        "total_cents": total,
        "by_tool": [{"tool": tool, "cents": cents} for tool, cents in sorted(by_tool.items())],
        "llm_tokens": _total_llm_tokens(ctx),
    }


def _total_llm_tokens(ctx: dict[str, Any]) -> int | None:
    total = 0
    for call in _llm_block(ctx):
        tokens = call.get("tokens")
        if isinstance(tokens, dict) and isinstance(tokens.get("total_tokens"), int):
            total += tokens["total_tokens"]
    return total or None


def _timings_block(ctx: dict[str, Any]) -> dict[str, Any]:
    """Real per-phase timings, which v1 left sitting unused in ``phase_log``."""
    metrics = ctx.get("metrics") or {}
    by_phase = {k: v for k, v in metrics.items() if isinstance(v, int)}

    tool_ms = sum(
        step.get("latency_ms") or 0
        for step in _steps_block(ctx)
        if isinstance(step.get("latency_ms"), int)
    )
    llm_ms = sum(
        call.get("latency_ms") or 0
        for call in _llm_block(ctx)
        if isinstance(call.get("latency_ms"), int)
    )

    return {
        "total_ms": sum(by_phase.values()) or None,
        "by_phase": by_phase,
        "tools_ms": tool_ms or None,
        "llm_ms": llm_ms or None,
    }


def _flags_block(ctx: dict[str, Any]) -> dict[str, Any]:
    flags = ctx.get("flags")
    if not isinstance(flags, dict):
        return {"user": {}, "system": {}}
    return {
        "user": summarize_value(flags.get("user") or {}),
        "system": summarize_value(flags.get("system") or {}),
    }


_BLOCKS: tuple[tuple[str, Callable[[dict[str, Any]], Any]], ...] = (
    ("run", _run_block),
    ("input", _input_block),
    ("output", _output_block),
    ("routing", _routing_block),
    ("steps", _steps_block),
    ("llm", _llm_block),
    ("evaluation", _evaluation_block),
    ("tool_reports", _tool_reports_block),
    ("cost", _cost_block),
    ("timings", _timings_block),
    ("flags", _flags_block),
)
