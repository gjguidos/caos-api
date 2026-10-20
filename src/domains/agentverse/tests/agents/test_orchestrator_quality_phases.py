"""Tests for the ORCHESTRATOR_QUALITY_PHASES_ENABLED gate.

Covers what apply_quality_gate decides, the loop's no-op accounting for the
phases it drops, and the ``@tool`` routing invariant that must survive with the
quality phases switched off.
"""

from __future__ import annotations

import pytest

from src.base.config.config import settings
from src.domains.agentverse.agents.orchestrator.built_in_phases.h_analyze import h_analyze
from src.domains.agentverse.agents.orchestrator.built_in_phases.h_plan import h_plan
from src.domains.agentverse.agents.orchestrator.quality_phases import apply_quality_gate
from src.domains.agentverse.agents.orchestrator.universal import UniversalOrchestrator

ALL_PHASES = ["analyze", "plan", "task", "refine", "evaluate"]


class FakeToolManager:
    def __init__(self, keys):
        self._keys = keys
        self.executed = []

    def list_tool_keys(self):
        return self._keys

    async def execute(self, **kwargs):
        self.executed.append(kwargs.get("key"))
        return {}


def _recording_handlers(awaited: list[str]) -> dict:
    def make(phase: str):
        async def handler(ctx, extra):
            awaited.append(phase)
            return ctx

        return handler

    return {phase: make(phase) for phase in ALL_PHASES}


def _set_flag(monkeypatch, value: bool) -> None:
    monkeypatch.setattr(settings.orchestrator, "orchestrator_quality_phases_enabled", value)


def _noop_phases(ctx) -> list[str]:
    return [entry["phase"] for entry in ctx["phase_log"] if entry.get("noop")]


def test_gate_drops_quality_phases_and_the_llm_brief_when_flag_off(monkeypatch):
    _set_flag(monkeypatch, False)
    extra: dict = {}

    kept = apply_quality_gate(_recording_handlers([]), extra)

    assert list(kept) == ["analyze", "plan", "task"]
    assert extra["allow_llm_brief"] is False


def test_gate_passes_everything_through_when_flag_on(monkeypatch):
    _set_flag(monkeypatch, True)
    handlers = _recording_handlers([])
    extra: dict = {}

    kept = apply_quality_gate(handlers, extra)

    assert kept == handlers
    assert extra["allow_llm_brief"] is True


@pytest.mark.asyncio
async def test_loop_records_dropped_phases_as_noops(monkeypatch):
    """The run report's timings.by_phase relies on skipped phases still being logged."""
    _set_flag(monkeypatch, False)
    awaited: list[str] = []
    extra: dict = {}

    ctx = await UniversalOrchestrator().plan_and_execute(
        handlers=apply_quality_gate(_recording_handlers(awaited), extra), ctx={}, extra=extra
    )

    assert awaited == ["analyze", "plan", "task"]
    assert _noop_phases(ctx) == ["refine", "evaluate"]
    assert ctx["metrics"]["refine_ms"] == 0
    assert ctx["metrics"]["evaluate_ms"] == 0


@pytest.mark.asyncio
async def test_loop_runs_all_phases_when_flag_on(monkeypatch):
    _set_flag(monkeypatch, True)
    awaited: list[str] = []
    extra: dict = {}

    ctx = await UniversalOrchestrator().plan_and_execute(
        handlers=apply_quality_gate(_recording_handlers(awaited), extra), ctx={}, extra=extra
    )

    assert awaited == ALL_PHASES
    assert _noop_phases(ctx) == []


@pytest.mark.asyncio
async def test_analyze_skips_the_llm_call_when_brief_shaping_is_off():
    tm = FakeToolManager(["draw", "chat"])

    await h_analyze(
        "analyze", {"raw_request": "hello"}, {"tool_manager": tm, "allow_llm_brief": False}
    )

    assert tm.executed == []


@pytest.mark.asyncio
async def test_analyze_calls_the_llm_when_brief_shaping_is_on():
    tm = FakeToolManager(["draw", "chat"])

    await h_analyze(
        "analyze", {"raw_request": "hello"}, {"tool_manager": tm, "allow_llm_brief": True}
    )

    assert tm.executed == ["analyze"]


@pytest.mark.asyncio
async def test_tool_token_still_routes_with_flag_off():
    tm = FakeToolManager(["draw", "chat"])
    ctx = {"raw_request": "@draw a cat"}
    extra = {"tool_manager": tm, "allow_llm_brief": False}

    await h_analyze("analyze", ctx, extra)
    await h_plan(ctx, extra)

    assert ctx["selected_tool"] == "draw"
    assert ctx["plan"]["steps"] == ["draw"]
    assert tm.executed == []
    # refine and evaluate read ctx["brief"]; it must survive the skipped LLM call
    assert ctx["brief"]["intent"]


@pytest.mark.asyncio
async def test_plain_chat_pins_to_chat_tool_with_flag_off():
    tm = FakeToolManager(["draw", "chat"])
    ctx = {"raw_request": "hello"}
    extra = {"tool_manager": tm, "allow_llm_brief": False}

    await h_analyze("analyze", ctx, extra)
    await h_plan(ctx, extra)

    assert ctx["selected_tool"] is None
    assert ctx["plan"]["steps"] == ["chat"]
    assert tm.executed == []
    # The deterministic fallback must fill the brief the LLM branch would have.
    assert ctx["brief"]["intent"] == "general"
    assert ctx["brief"]["acceptance"]["quality_threshold"] == 0.8
    assert ctx["route_confidence"] == 0.35
