"""Tests for the declared-output projection into the ``outputs`` namespace.

Each successful tool step stores two things:

- ``outputs.<step_id>``: a clean ``{key: value}`` projection of the tool's
  declared outputs (the binding/templating namespace). A downstream step binds
  ``${outputs.<step_id>.<key>}`` against these projected keys.
- ``display_outputs.<step_id>``: the raw ``{"tool", "env"}`` wrapper the display
  layer reads.

Binding is strict: only declared keys resolve; an undeclared key raises
``MissingTemplateValueError``.
"""

from __future__ import annotations

from unittest import mock

import pytest

from src.domains.agentverse.tools.spec import ToolOutputSpec
from src.domains.agentverse.tools.workflow_tool.helpers import declared_outputs as declared
from src.domains.agentverse.tools.workflow_tool.helpers.declared_outputs import (
    project_outputs,
    warn_unresolved_outputs,
)
from src.domains.agentverse.tools.workflow_tool.helpers.templating import (
    MissingTemplateValueError,
    render_template,
)
from src.domains.agentverse.tools.workflow_tool.libs.engine.execute.tool import (
    _successful_tool_result,
)
from src.domains.agentverse.tools.workflow_tool.libs.engine.render_ctx import render_ctx
from src.domains.agentverse.tools.workflow_tool.models import WorkflowRun, WorkflowStep, now_utc_iso

_WRITER_OUTPUTS = [
    ToolOutputSpec(key="answer", label="Answer", type="string", path="answer"),
]


def _step(step_id: str = "write", **kwargs) -> WorkflowStep:
    return WorkflowStep(id=step_id, type="tool", command="@writer --text=x", **kwargs)


class TestProjectOutputs:
    def test_projects_declared_key_from_env(self) -> None:
        projected = project_outputs({"answer": "Hello world"}, _WRITER_OUTPUTS)
        assert projected == {"answer": "Hello world"}

    def test_nested_path_is_walked(self) -> None:
        outputs = [
            ToolOutputSpec(key="model", label="Model", type="string", path="addons.usage.model")
        ]
        projected = project_outputs({"addons": {"usage": {"model": "gpt"}}}, outputs)
        assert projected == {"model": "gpt"}

    def test_missing_path_yields_none(self) -> None:
        projected = project_outputs({"other": "x"}, _WRITER_OUTPUTS)
        assert projected == {"answer": None}

    def test_non_dict_env_yields_empty(self) -> None:
        assert project_outputs("plain string", _WRITER_OUTPUTS) == {}

    def test_no_declared_outputs_yields_empty(self) -> None:
        assert project_outputs({"answer": "x"}, []) == {}


class TestWarnUnresolvedOutputs:
    """Only a path that is *absent* is a spec error.

    The projection binds `None` for both an absent path and a path holding a
    real `null`, so the warning resolves against `env` rather than reading the
    projection back - otherwise every tool that legitimately returns `null` for
    a declared field logs an author-error warning on every step.
    """

    def _warnings(self, env, outputs) -> list:
        with mock.patch.object(declared, "AGENTVERSE_LOGGER") as logger:
            warn_unresolved_outputs("writer", env, outputs)
            return logger.warning.call_args_list

    def test_an_absent_path_is_reported_with_its_path(self) -> None:
        calls = self._warnings({"other": "x"}, _WRITER_OUTPUTS)

        assert len(calls) == 1
        assert calls[0].kwargs["extra"]["unresolved"] == [{"key": "answer", "path": "answer"}]

    def test_a_path_present_but_holding_none_is_not_reported(self) -> None:
        assert self._warnings({"answer": None}, _WRITER_OUTPUTS) == []

    def test_a_resolved_path_is_not_reported(self) -> None:
        assert self._warnings({"answer": "x"}, _WRITER_OUTPUTS) == []

    def test_a_non_dict_env_is_not_reported(self) -> None:
        """The step returned no env shape to have a stale path against."""
        assert self._warnings("plain string", _WRITER_OUTPUTS) == []


class TestSuccessfulToolResult:
    def test_splits_projected_binding_from_display_wrapper(self) -> None:
        result = _successful_tool_result(
            step=_step(),
            tool_key="writer",
            env={"answer": "Persisted text"},
            outputs=_WRITER_OUTPUTS,
        )

        # Binding namespace: clean projected keys, no {tool, env} wrapper.
        assert result.state_patch["outputs.write"] == {"answer": "Persisted text"}

        # Display namespace: the raw wrapper the display layer reads.
        display = result.state_patch["display_outputs.write"]
        assert display == {"tool": "writer", "env": {"answer": "Persisted text"}}

        # The step's immediate output is the display wrapper (event/display path).
        assert result.output == display

    def test_declared_key_is_bindable_downstream(self) -> None:
        now = now_utc_iso()
        result = _successful_tool_result(
            step=_step(),
            tool_key="writer",
            env={"answer": "Chained value"},
            outputs=_WRITER_OUTPUTS,
        )
        run = WorkflowRun(run_id="r1", created_at=now, updated_at=now)
        run.state["outputs"] = {"write": result.state_patch["outputs.write"]}

        rendered = render_template(
            "@summarize --text=${outputs.write.answer}", render_ctx(run), strict=True
        )
        assert rendered == "@summarize --text=Chained value"

    def test_undeclared_key_raises_strict(self) -> None:
        now = now_utc_iso()
        result = _successful_tool_result(
            step=_step(),
            tool_key="writer",
            env={"answer": "Chained value"},
            outputs=_WRITER_OUTPUTS,
        )
        run = WorkflowRun(run_id="r1", created_at=now, updated_at=now)
        run.state["outputs"] = {"write": result.state_patch["outputs.write"]}

        with pytest.raises(MissingTemplateValueError):
            render_template("@summarize --text=${outputs.write.text}", render_ctx(run), strict=True)
