from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypedDict

from .phases import Artifact, EvalResult


class OrchestrationContext(TypedDict, total=False):
    role: str  # role name (e.g., "general", "writer")
    request: str  # latest user input
    history: list[dict[str, Any]]  # conversation history messages

    # Phase artifacts
    brief: dict[str, Any]  # output of Analyze
    plan: dict[str, Any]  # output of Plan
    artifact: Artifact  # output of Task/Refine
    sources: list[dict[str, Any]]
    eval: EvalResult

    # Ops & observability
    metrics: dict[str, Any]  # per-phase ms, token budgets, retry counts
    phase_log: list[dict[str, Any]]

    # Run identity and telemetry sinks, seeded at loop start. The tool trace is
    # also threaded into tool_kwargs so execute_router can append to it; the
    # names are underscore-prefixed because they are plumbing, not phase output.
    run_id: str
    started_at: str
    ended_at: str
    _tool_trace: list[dict[str, Any]]
    _llm_calls: list[dict[str, Any]]

    # Clarification + assumptions
    missing: list[str]
    assumptions: list[str]


# Phase handler signature: (ctx, extra) -> ctx
PhaseHandler = Callable[[OrchestrationContext, dict[str, Any]], Awaitable[OrchestrationContext]]
