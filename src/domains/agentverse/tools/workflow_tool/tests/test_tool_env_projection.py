"""Tests for the tool env -> `${outputs.*}` projection in `execute_tool`.

The runner contract is `(ok, tool_env)` where the env is flat - the envelope layer
has already peeled any nested `result`/`data`. `execute_tool` therefore resolves
declared output `path`s straight against what the runner returned. These tests
pin that: a tool env carrying its own `data` key (green_claim, the MCP
proxy) must keep every sibling key bindable, and a failed runner call must land
as a failed step carrying the tool's error.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.domains.agentverse.tools.spec import ToolOutputSpec
from src.domains.agentverse.tools.workflow_tool.libs.engine.execute import tool as tool_module
from src.domains.agentverse.tools.workflow_tool.libs.engine.execute.tool import execute_tool
from src.domains.agentverse.tools.workflow_tool.models import (
    WorkflowDefinition,
    WorkflowRun,
    WorkflowStep,
    now_utc_iso,
)


def _run() -> WorkflowRun:
    now = now_utc_iso()
    return WorkflowRun(run_id="r1", created_at=now, updated_at=now)


async def _run_step(
    monkeypatch: pytest.MonkeyPatch,
    *,
    env: Any,
    ok: bool = True,
    outputs: list[ToolOutputSpec] | None = None,
):
    monkeypatch.setattr(tool_module, "resolve_tool_outputs", lambda _key: outputs or [])

    step = WorkflowStep(id="s1", type="tool", command="@analyzer --run")

    async def runner(tool_key: str, kwargs: dict) -> tuple[bool, Any]:
        return ok, env

    return await execute_tool(
        run=_run(),
        wf=WorkflowDefinition(steps=[step]),
        step=step,
        tool_runner=runner,
        context={},
    )


class TestEnvCarryingData:
    @pytest.mark.asyncio
    async def test_sibling_keys_stay_bindable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # green_claim returns {answer, data}; the MCP proxy add more
        # siblings. All declare `path: "answer"`.
        result = await _run_step(
            monkeypatch,
            env={"answer": "report text", "data": {"claims": []}},
            outputs=[ToolOutputSpec(key="answer", label="Answer", type="string", path="answer")],
        )

        assert result.ok
        assert result.state_patch["outputs.s1"]["answer"] == "report text"

    @pytest.mark.asyncio
    async def test_data_itself_is_bindable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result = await _run_step(
            monkeypatch,
            env={"answer": "emitted", "data": {"slots": {"reference": "f_abc"}}},
            outputs=[
                ToolOutputSpec(key="slots", label="Slots", type="object", path="data.slots"),
            ],
        )

        assert result.state_patch["outputs.s1"]["slots"] == {"reference": "f_abc"}


class TestFailureSignals:
    @pytest.mark.asyncio
    async def test_runner_failure_surfaces_the_tool_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        result = await _run_step(
            monkeypatch,
            ok=False,
            env={"error": "upstream exploded", "success": False},
        )

        assert not result.ok
        assert result.error == "upstream exploded"

    @pytest.mark.asyncio
    async def test_env_success_false_fails_the_step(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result = await _run_step(
            monkeypatch,
            ok=True,
            env={"success": False, "error": "tool said no"},
        )

        assert not result.ok
        assert result.error == "tool said no"


class _FailingProxyClient:
    """A proxy client whose remote tool reports a failure."""

    def __init__(self, error: str) -> None:
        self._error = error

    async def execute_tool(self, *, tool_name: str, method: str, arguments: dict) -> Any:
        from src.base.integrations.mcp.models import MCPToolExecuteResponse

        return MCPToolExecuteResponse(success=False, result=None, error=self._error)


class TestRemoteToolFailure:
    """The failure fixtures above, driven through the real proxy path.

    The env the engine sees is produced by `MCPProxyTool.execute` and unwrapped
    by the nested tool runner (`pure_prep._tool_runner`), so this pins the whole
    chain rather than a hand-written env.
    """

    @pytest.mark.asyncio
    async def test_the_remote_reason_becomes_the_step_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.base.integrations.mcp.proxy_tool import MCPProxyTool
        from src.domains.agentverse.tools.spec import ToolSpec
        from src.domains.agentverse.tools.tool_manager_v1.envelope import as_envelope

        tool = MCPProxyTool(
            mcp_client=_FailingProxyClient("remote tool rejected the input"),
            tool_spec=ToolSpec(key="analyzer", name="Analyzer Tool"),
        )

        async def runner(tool_key: str, kwargs: dict) -> tuple[bool, Any]:
            envelope = as_envelope(await tool.execute(text=kwargs["text"]))
            return bool(envelope.get("success", True)), envelope.get("result")

        monkeypatch.setattr(tool_module, "resolve_tool_outputs", lambda _key: [])
        step = WorkflowStep(id="s1", type="tool", command="@analyzer --run")

        result = await execute_tool(
            run=_run(),
            wf=WorkflowDefinition(steps=[step]),
            step=step,
            tool_runner=runner,
            context={},
        )

        assert not result.ok
        assert result.error == "remote tool rejected the input"
        assert result.output["env"]["error"] == "remote tool rejected the input"
