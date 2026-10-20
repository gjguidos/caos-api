"""Tests for image passing between workflow steps via ``file_bindings`` (card 5).

A step binds an upstream image output into a named file slot; the engine
resolves each binding against the outputs namespace and injects the image(s)
into the consuming tool's ``files`` kwargs - a channel parallel to the text
command. The files channel is strict-fail: an unresolvable reference or a
non-image resolved value fails the step.
"""

from __future__ import annotations

import pytest

from src.domains.agentverse.tools.workflow_tool.helpers.templating import (
    MissingTemplateValueError,
    resolve_single_reference,
)
from src.domains.agentverse.tools.workflow_tool.models import (
    WorkflowDefinition,
    WorkflowRun,
    WorkflowStep,
    now_utc_iso,
)

_PNG_URI = "data:image/png;base64,AA=="


def _run_with_outputs(outputs: dict) -> WorkflowRun:
    now = now_utc_iso()
    run = WorkflowRun(run_id="r1", created_at=now, updated_at=now)
    run.state["outputs"] = outputs
    return run


async def _execute(run: WorkflowRun, step: WorkflowStep) -> tuple:
    from src.domains.agentverse.tools.workflow_tool.libs.engine.execute.tool import execute_tool

    captured: dict = {}

    async def fake_runner(tool_key: str, kwargs: dict):
        captured["tool_key"] = tool_key
        captured["kwargs"] = kwargs
        return True, {"success": True, "ok": True}

    result = await execute_tool(
        run=run,
        wf=WorkflowDefinition(steps=[step]),
        step=step,
        tool_runner=fake_runner,
        context={},
    )
    return result, captured


class TestResolveSingleReference:
    def test_returns_raw_resolved_object(self) -> None:
        image = {"url": _PNG_URI}
        ctx = {"outputs": {"p": {"image": image}}}
        assert resolve_single_reference("${outputs.p.image}", ctx) is image

    def test_surrounding_whitespace_is_tolerated(self) -> None:
        ctx = {"outputs": {"p": {"image": 1}}}
        assert resolve_single_reference("  ${outputs.p.image}  ", ctx) == 1

    def test_interpolated_string_rejected(self) -> None:
        ctx = {"outputs": {"p": {"image": 1}}}
        with pytest.raises(MissingTemplateValueError):
            resolve_single_reference("prefix ${outputs.p.image}", ctx)

    def test_two_references_rejected(self) -> None:
        ctx = {"a": 1, "b": 2}
        with pytest.raises(MissingTemplateValueError):
            resolve_single_reference("${a}${b}", ctx)

    def test_plain_string_rejected(self) -> None:
        with pytest.raises(MissingTemplateValueError):
            resolve_single_reference("outputs.p.image", {})

    def test_missing_key_raises(self) -> None:
        with pytest.raises(MissingTemplateValueError, match="outputs.p.image"):
            resolve_single_reference("${outputs.p.image}", {"outputs": {}})


class TestFileBindingsModel:
    def test_empty_slot_name_rejected(self) -> None:
        with pytest.raises(ValueError, match="slot names"):
            WorkflowStep(
                id="s", type="tool", command="@t", file_bindings={" ": "${outputs.p.image}"}
            )

    def test_file_bindings_rejected_on_non_tool_steps(self) -> None:
        with pytest.raises(ValueError, match="file_bindings"):
            WorkflowStep(
                id="s",
                type="pause",
                file_bindings={"source": "${outputs.p.image}"},
            )

    def test_round_trips_through_definition_snapshot(self) -> None:
        wf = WorkflowDefinition(
            steps=[
                WorkflowStep(id="p", type="tool", command="@producer"),
                WorkflowStep(
                    id="sink",
                    type="tool",
                    command="@consumer",
                    depends_on=["p"],
                    file_bindings={"source": "${outputs.p.image}"},
                ),
            ]
        )

        snapshot = wf.model_dump()
        restored = WorkflowDefinition.model_validate(snapshot)

        assert restored.steps[1].file_bindings == {"source": "${outputs.p.image}"}


class TestExecuteToolFileBindings:
    @pytest.mark.asyncio
    async def test_single_binding_lands_in_files_kwargs(self) -> None:
        run = _run_with_outputs({"p": {"image": {"url": _PNG_URI}}})
        step = WorkflowStep(
            id="sink",
            type="tool",
            command="@consumer --go",
            file_bindings={"source": "${outputs.p.image}"},
        )

        result, captured = await _execute(run, step)

        assert result.ok
        assert captured["kwargs"]["files"] == [
            {"slot": "source", "url": _PNG_URI, "name": "p.image.png", "mime": "image/png"}
        ]

    @pytest.mark.asyncio
    async def test_two_slots_receive_the_right_images(self) -> None:
        run = _run_with_outputs(
            {
                "crop": {"image": {"url": "data:image/png;base64,QQ=="}},
                "style": {"image": {"url": "data:image/jpeg;base64,Ug=="}},
            }
        )
        step = WorkflowStep(
            id="sink",
            type="tool",
            command="@consumer --go",
            file_bindings={
                "source": "${outputs.crop.image}",
                "reference": "${outputs.style.image}",
            },
        )

        result, captured = await _execute(run, step)

        assert result.ok
        files = captured["kwargs"]["files"]
        by_slot = {f["slot"]: f for f in files}
        assert set(by_slot) == {"source", "reference"}
        assert by_slot["source"]["url"] == "data:image/png;base64,QQ=="
        assert by_slot["reference"]["url"] == "data:image/jpeg;base64,Ug=="
        assert by_slot["reference"]["mime"] == "image/jpeg"

    @pytest.mark.asyncio
    async def test_list_valued_output_fills_the_slot_with_indexed_names(self) -> None:
        run = _run_with_outputs(
            {"p": {"images": [{"url": "data:image/png;base64,QQ=="}, {"url": _PNG_URI}]}}
        )
        step = WorkflowStep(
            id="sink",
            type="tool",
            command="@consumer --go",
            file_bindings={"source": "${outputs.p.images}"},
        )

        result, captured = await _execute(run, step)

        assert result.ok
        files = captured["kwargs"]["files"]
        assert [f["slot"] for f in files] == ["source", "source"]
        assert [f["name"] for f in files] == ["p.images.0.png", "p.images.1.png"]
        assert [f["url"] for f in files] == ["data:image/png;base64,QQ==", _PNG_URI]

    @pytest.mark.asyncio
    async def test_raw_image_output_shape_is_converted_to_data_uri(self) -> None:
        run = _run_with_outputs({"p": {"image": {"data": "AA==", "mime": "image/png"}}})
        step = WorkflowStep(
            id="sink",
            type="tool",
            command="@consumer --go",
            file_bindings={"source": "${outputs.p.image}"},
        )

        result, captured = await _execute(run, step)

        assert result.ok
        (file,) = captured["kwargs"]["files"]
        assert file["url"] == _PNG_URI
        assert file["mime"] == "image/png"

    @pytest.mark.asyncio
    async def test_unresolvable_reference_fails_the_step(self) -> None:
        run = _run_with_outputs({})
        step = WorkflowStep(
            id="sink",
            type="tool",
            command="@consumer --go",
            file_bindings={"source": "${outputs.missing.image}"},
        )

        result, captured = await _execute(run, step)

        assert not result.ok
        assert result.status == "EXECUTE TOOL FAILED"
        assert "source" in result.error
        assert "kwargs" not in captured  # tool never invoked

    @pytest.mark.asyncio
    async def test_non_image_value_fails_with_shape_error(self) -> None:
        run = _run_with_outputs({"p": {"answer": "just text"}})
        step = WorkflowStep(
            id="sink",
            type="tool",
            command="@consumer --go",
            file_bindings={"source": "${outputs.p.answer}"},
        )

        result, captured = await _execute(run, step)

        assert not result.ok
        assert "did not resolve to an image" in result.error
        assert "slot 'source'" in result.error
        assert "kwargs" not in captured

    @pytest.mark.asyncio
    async def test_empty_list_fails_with_shape_error(self) -> None:
        run = _run_with_outputs({"p": {"images": []}})
        step = WorkflowStep(
            id="sink",
            type="tool",
            command="@consumer --go",
            file_bindings={"source": "${outputs.p.images}"},
        )

        result, _captured = await _execute(run, step)

        assert not result.ok
        assert "did not resolve to an image" in result.error

    @pytest.mark.asyncio
    async def test_step_without_bindings_has_no_files_key(self) -> None:
        run = _run_with_outputs({"p": {"image": {"url": _PNG_URI}}})
        step = WorkflowStep(id="sink", type="tool", command="@consumer --go")

        result, captured = await _execute(run, step)

        assert result.ok
        assert "files" not in captured["kwargs"]
