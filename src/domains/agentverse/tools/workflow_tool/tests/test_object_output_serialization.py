"""Tests for ``object``/``list`` output serialization into tool commands (card 4).

A structured (``object`` or ``list``) binding must reach the consuming step as
real JSON, never a Python ``str(dict)`` repr. The seam is
``_encode_command_value`` (``execute/tool.py``), threaded into ``render_template``
as its ``encode`` callable. The consuming side is unchanged production code: the
CLI splitter captures the JSON blob and ``coerce_val`` parses an ``object``/
``array`` param back into a dict/list.

These tests exercise that full seam end-to-end (render -> real CLI parse -> dict),
plus one ``execute_tool`` integration proving an object binding lands in the
downstream command channel.
"""

from __future__ import annotations

import json

import pytest

from src.domains.agentverse.tools.spec import ToolOutputSpec
from src.domains.agentverse.tools.tool_manager_v1.cli_overrides import extract_cli_overrides
from src.domains.agentverse.tools.tool_manager_v1.params import resolve_param_name
from src.domains.agentverse.tools.workflow_tool.libs.engine.execute.tool import (
    _encode_command_value,
    _render_command,
    _successful_tool_result,
    execute_tool,
)
from src.domains.agentverse.tools.workflow_tool.models import (
    WorkflowDefinition,
    WorkflowRun,
    WorkflowStep,
    now_utc_iso,
)
from src.domains.agentverse.utils.coercion import coerce_val
from src.domains.agentverse.utils.strip_quotes import strip_quotes

_BOX = {"x": 12, "y": 20, "w": 3, "h": 4}


def _run_with_outputs(outputs: dict) -> WorkflowRun:
    now = now_utc_iso()
    run = WorkflowRun(run_id="r1", created_at=now, updated_at=now)
    run.state["outputs"] = outputs
    return run


def _parse_cli(command: str, specs: dict) -> dict:
    """Feed a rendered command back through the real CLI override parser."""
    _clean, overrides = extract_cli_overrides(
        command,
        param_specs=specs,
        flag_re=None,
        resolve_param_name=resolve_param_name,
        strip_quotes=strip_quotes,
        coerce_val=coerce_val,
    )
    return overrides


class TestEncodeCommandValue:
    def test_dict_is_compact_json(self) -> None:
        assert _encode_command_value(_BOX) == '{"x":12,"y":20,"w":3,"h":4}'

    def test_list_is_json_not_python_repr(self) -> None:
        assert _encode_command_value(["a", "b"]) == '["a","b"]'
        assert "'" not in _encode_command_value(["a", "b"])

    def test_scalars_keep_str_projection(self) -> None:
        assert _encode_command_value("faucet") == "faucet"
        assert _encode_command_value(42) == "42"
        assert _encode_command_value(True) == "True"


class TestRenderSeamRoundTrip:
    def test_object_binding_renders_json_the_consumer_parses_back(self) -> None:
        run = _run_with_outputs({"detect": {"box": _BOX, "label": "faucet"}})
        step = WorkflowStep(
            id="consume",
            type="tool",
            command="@consumer --box=${outputs.detect.box} --label=${outputs.detect.label}",
        )

        command = _render_command(run=run, step=step)
        assert isinstance(command, str)

        # An object is never str(dict)-stringified (no single-quoted Python repr).
        assert "'x'" not in command
        assert '{"x":12,"y":20,"w":3,"h":4}' in command

        overrides = _parse_cli(command, {"box": {"type": "object"}, "label": {"type": "string"}})
        assert overrides["box"] == _BOX
        assert overrides["label"] == "faucet"

    def test_list_binding_round_trips_as_array(self) -> None:
        run = _run_with_outputs({"detect": {"tags": ["a", "b", "c"]}})
        step = WorkflowStep(
            id="consume", type="tool", command="@consumer --tags=${outputs.detect.tags}"
        )

        command = _render_command(run=run, step=step)
        assert isinstance(command, str)

        overrides = _parse_cli(command, {"tags": {"type": "array"}})
        assert overrides["tags"] == ["a", "b", "c"]


class TestProjectionStoresRealObject:
    def test_object_output_is_stored_as_dict_not_string(self) -> None:
        outputs = [ToolOutputSpec(key="box", label="Box", type="object", path="box")]
        result = _successful_tool_result(
            step=WorkflowStep(id="detect", type="tool", command="@detector --run"),
            tool_key="detector",
            env={"box": _BOX},
            outputs=outputs,
        )
        stored = result.state_patch["outputs.detect"]["box"]
        assert stored == _BOX
        assert isinstance(stored, dict)


class TestExecuteToolObjectHandoff:
    @pytest.mark.asyncio
    async def test_object_binding_reaches_downstream_command_channel(self) -> None:
        run = _run_with_outputs({"detect": {"box": _BOX}})
        consumer = WorkflowStep(
            id="consume", type="tool", command="@consumer --box=${outputs.detect.box}"
        )
        wf = WorkflowDefinition(steps=[consumer])

        captured: dict = {}

        async def fake_runner(tool_key: str, kwargs: dict):
            captured["tool_key"] = tool_key
            captured["command"] = kwargs["text"]
            return True, {"success": True, "ok": True}

        result = await execute_tool(
            run=run, wf=wf, step=consumer, tool_runner=fake_runner, context={}
        )

        assert result.ok
        assert captured["tool_key"] == "consumer"
        assert '{"x":12,"y":20,"w":3,"h":4}' in captured["command"]

        overrides = _parse_cli(captured["command"], {"box": {"type": "object"}})
        assert overrides["box"] == _BOX
        # Prove it really is the decoded object, resilient to key ordering.
        assert json.loads('{"x":12,"y":20,"w":3,"h":4}') == overrides["box"]
