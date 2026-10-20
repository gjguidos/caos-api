"""Why a blocked tool call was blocked must reach the caller.

``execute_router`` knows exactly which gate closed. It used to return a bare
``None`` for a policy veto or a failed prep, so a nested workflow step surfaced
an opaque empty envelope. These tests pin the reason travelling back out:
``evaluate_policy`` names the gate, and the nested tool runner relays it.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.domains.agentverse.tools.tool_manager_v1.execution.envelope import error_envelope
from src.domains.agentverse.tools.tool_manager_v1.execution.pure_prep import (
    inject_tool_runner_if_needed,
)
from src.domains.agentverse.tools.tool_manager_v1.policy import evaluate_policy


class _Tool:
    pass


class _Agent:
    id = "agent-1"
    name = "Orchestrator Agent"
    type = "orchestration"
    capabilities: set[str] = set()
    metadata: dict[str, Any] = {}
    # dep_map reads every injectable off the agent unconditionally.
    llm = image = video = audio = db = cache = vectordb = messaging = None
    http: dict[str, Any] = {}


def _factories(*keys: str) -> dict[str, Any]:
    return {k: {"cls": _Tool} for k in keys}


class TestPolicyReason:
    def test_unequipped_tool_names_tool_and_agent(self) -> None:
        reason = evaluate_policy(_Agent(), _factories("workflow", "writer"), "image_source")

        assert reason is not None
        assert "image_source" in reason
        assert "not equipped" in reason
        assert "Orchestrator Agent" in reason

    def test_unequipped_tool_lists_what_is_equipped(self) -> None:
        reason = evaluate_policy(_Agent(), _factories("workflow", "writer"), "image_source")

        assert "workflow, writer" in reason

    def test_equipped_tool_is_allowed(self) -> None:
        assert evaluate_policy(_Agent(), _factories("writer"), "writer") is None

    def test_deny_tools_names_the_gate(self) -> None:
        agent = _Agent()
        agent.metadata = {"deny_tools": ["writer"]}

        reason = evaluate_policy(agent, _factories("writer"), "writer")

        assert reason is not None
        assert "deny_tools" in reason

    def test_allow_tools_names_the_gate(self) -> None:
        agent = _Agent()
        agent.metadata = {"allow_tools": ["writer"]}

        reason = evaluate_policy(agent, _factories("writer", "image_source"), "image_source")

        assert reason is not None
        assert "allow_tools" in reason

    def test_nested_workflow_blocked_when_not_enabled(self) -> None:
        reason = evaluate_policy(
            _Agent(),
            _factories("workflow"),
            "workflow",
            context={"workflow_depth": 1, "allow_nested_workflows": False},
        )

        assert reason is not None
        assert "nested workflows" in reason


class _Manager:
    def __init__(self, envelope: Any = None):
        self.agent = _Agent()
        self._envelope = envelope
        self.executed: list[str] = []

    async def execute(self, socket_emitter, key: str, **kwargs):
        self.executed.append(key)
        return self._envelope


class _NeedsRunner:
    requires_tool_runner = True


def _runner(manager: _Manager):
    ctx: dict[str, Any] = {}
    inject_tool_runner_if_needed(
        None,
        manager,
        inst=_NeedsRunner(),
        effective_ctx=ctx,
        session_id="s1",
        request="@workflow --op=start",
        policy=None,
    )
    return ctx["tool_runner"]


class TestNestedRunnerRelaysTheReason:
    @pytest.mark.asyncio
    async def test_error_envelope_reaches_the_workflow_step(self) -> None:
        manager = _Manager(envelope=error_envelope("Tool 'image_source' is not equipped."))

        ok, payload = await _runner(manager)("image_source", {})

        assert ok is False
        assert payload["error"] == "Tool 'image_source' is not equipped."

    @pytest.mark.asyncio
    async def test_success_envelope_unwraps_to_the_result(self) -> None:
        manager = _Manager(envelope={"success": True, "result": {"answer": "hi"}})

        ok, payload = await _runner(manager)("writer", {})

        assert ok is True
        assert payload == {"answer": "hi"}
        assert manager.executed == ["writer"]
