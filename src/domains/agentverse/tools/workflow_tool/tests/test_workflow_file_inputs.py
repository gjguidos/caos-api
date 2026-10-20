"""Tests for workflow file inputs (WBP-1314 follow-up).

A workflow declares a run input of kind ``file``; the run starts with an opaque
``file_id`` string which ``materialize_file_inputs`` validates against the
caller and swaps for a resolved file dict in ``run.inputs``. A step's
``file_bindings`` may then reference ``${inputs.<key>}`` so the uploaded file
lands in the consuming tool's ``files`` channel - SAS URL minted downstream,
exactly like chat-attached files.
"""

from __future__ import annotations

import pytest

from src.base.services.file_service import (
    FileMetadata,
    FileMissingError,
    FileOwnershipError,
    mime_accepted,
)
from src.domains.agentverse.tools.base import ToolExecutionError
from src.domains.agentverse.tools.workflow_tool.helpers.file_inputs import (
    materialize_file_inputs,
)
from src.domains.agentverse.tools.workflow_tool.models import (
    WorkflowDefinition,
    WorkflowRun,
    WorkflowStep,
    now_utc_iso,
)
from src.domains.agentverse.workflows.spec import WorkflowInputField


def _file_field(**overrides) -> WorkflowInputField:
    payload = {"key": "pic", "label": "Picture", "input": "file"}
    payload.update(overrides)
    return WorkflowInputField(**payload)


def _meta(**overrides) -> FileMetadata:
    payload = {
        "file_id": "f_abc",
        "name": "photo.png",
        "mime": "image/png",
        "size": 68,
        "sha256": "s",
        "user_id": "u1",
    }
    payload.update(overrides)
    return FileMetadata(**payload)


class _FakeFileService:
    def __init__(self, files: dict[str, FileMetadata]):
        self._files = files

    async def get_metadata(self, file_id: str, requesting_user_id: str) -> FileMetadata:
        meta = self._files.get(file_id)
        if meta is None:
            raise FileMissingError(file_id)
        if meta.user_id != requesting_user_id:
            raise FileOwnershipError(f"File '{file_id}' not owned by user")
        return meta


# ----------------------------------------------------------------- mime matcher


class TestMimeAccepted:
    def test_exact_match(self) -> None:
        assert mime_accepted("image/png", ["image/png"])

    def test_mismatch(self) -> None:
        assert not mime_accepted("image/png", ["image/jpeg"])

    def test_type_wildcard(self) -> None:
        assert mime_accepted("image/png", ["image/*"])
        assert not mime_accepted("application/pdf", ["image/*"])

    def test_any_wildcard(self) -> None:
        assert mime_accepted("application/pdf", ["*/*"])

    def test_empty_or_none_accepts_anything(self) -> None:
        assert mime_accepted("image/png", [])
        assert mime_accepted("image/png", None)

    def test_case_insensitive(self) -> None:
        assert mime_accepted("image/png", ["IMAGE/PNG"])
        assert mime_accepted("image/png", ["Image/Png"])
        assert mime_accepted("image/png", ["IMAGE/*"])

    def test_whitespace_trimmed(self) -> None:
        assert mime_accepted("image/png", [" image/png "])
        assert mime_accepted("image/png", ["  image/*  "])
        assert mime_accepted("image/png", [" IMAGE/PNG "])
        assert mime_accepted("application/pdf", ["*/*"])


# ----------------------------------------------------------------- step model


class TestWorkflowStepFileBindings:
    def test_valid_file_bindings_accepted(self) -> None:
        step = WorkflowStep(
            id="s1",
            command="@tool",
            file_bindings={"slot1": "${outputs.p}", "slot2": "${inputs.pic}"},
        )
        assert step.file_bindings == {"slot1": "${outputs.p}", "slot2": "${inputs.pic}"}

    def test_empty_slot_name_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            WorkflowStep(id="s1", command="@tool", file_bindings={"": "${outputs.p}"})

    def test_whitespace_slot_name_rejected(self) -> None:
        with pytest.raises(ValueError, match="must not be empty"):
            WorkflowStep(id="s1", command="@tool", file_bindings={"   ": "${outputs.p}"})

    def test_slot_names_stripped(self) -> None:
        step = WorkflowStep(
            id="s1",
            command="@tool",
            file_bindings={" slot1 ": "${outputs.p}"},
        )
        assert "slot1" in step.file_bindings
        assert " slot1 " not in step.file_bindings

    def test_collision_on_stripped_slot_names_rejected(self) -> None:
        with pytest.raises(ValueError, match="defined multiple times"):
            WorkflowStep(
                id="s1",
                command="@tool",
                file_bindings={"source": "${outputs.p}", " source ": "${outputs.q}"},
            )

    def test_collision_case_sensitive(self) -> None:
        step = WorkflowStep(
            id="s1",
            command="@tool",
            file_bindings={"Source": "${outputs.p}", "source": "${outputs.q}"},
        )
        assert len(step.file_bindings) == 2
        assert "Source" in step.file_bindings
        assert "source" in step.file_bindings


# ----------------------------------------------------------------- input model


class TestWorkflowInputFieldFileKind:
    def test_is_file(self) -> None:
        assert _file_field().is_file
        assert not WorkflowInputField(key="t", label="T", input="text").is_file

    def test_accepted_mimes_read_from_constraints(self) -> None:
        field = _file_field(constraints={"accepted_mimes": ["image/png", "image/*"]})
        assert field.accepted_mimes == ["image/png", "image/*"]

    def test_accepted_mimes_default_empty(self) -> None:
        assert _file_field().accepted_mimes == []
        assert _file_field(constraints={}).accepted_mimes == []

    def test_non_list_accepted_mimes_rejected(self) -> None:
        with pytest.raises(ValueError, match="accepted_mimes"):
            _file_field(constraints={"accepted_mimes": "image/png"})

    def test_non_string_entries_rejected(self) -> None:
        with pytest.raises(ValueError, match="accepted_mimes"):
            _file_field(constraints={"accepted_mimes": [1]})

    def test_non_file_inputs_do_not_validate_constraints(self) -> None:
        field = WorkflowInputField(
            key="t", label="T", input="text", constraints={"accepted_mimes": "whatever"}
        )
        assert not field.is_file


# ----------------------------------------------------------------- materialize


class TestMaterializeFileInputs:
    @pytest.mark.asyncio
    async def test_swaps_file_id_for_resolved_dict(self) -> None:
        service = _FakeFileService({"f_abc": _meta()})

        result = await materialize_file_inputs(
            inputs={"pic": "f_abc", "text": "hello"},
            declared=[_file_field(), WorkflowInputField(key="text", label="T", input="text")],
            file_service=service,
            user_id="u1",
        )

        assert result["pic"] == {
            "file_id": "f_abc",
            "user_id": "u1",
            "name": "photo.png",
            "mime": "image/png",
            "size": 68,
        }
        assert result["text"] == "hello"

    @pytest.mark.asyncio
    async def test_no_file_fields_is_passthrough(self) -> None:
        inputs = {"text": "hello"}
        result = await materialize_file_inputs(
            inputs=inputs,
            declared=[WorkflowInputField(key="text", label="T", input="text")],
            file_service=None,
            user_id=None,
        )
        assert result is inputs

    @pytest.mark.asyncio
    async def test_required_missing_raises(self) -> None:
        with pytest.raises(ToolExecutionError, match="Required file input 'pic'"):
            await materialize_file_inputs(
                inputs={},
                declared=[_file_field(required=True)],
                file_service=_FakeFileService({}),
                user_id="u1",
            )

    @pytest.mark.asyncio
    async def test_optional_empty_is_dropped(self) -> None:
        result = await materialize_file_inputs(
            inputs={"pic": ""},
            declared=[_file_field(required=False)],
            file_service=_FakeFileService({}),
            user_id="u1",
        )
        assert "pic" not in result

    @pytest.mark.asyncio
    async def test_list_value_rejected(self) -> None:
        with pytest.raises(ToolExecutionError, match="single file_id string"):
            await materialize_file_inputs(
                inputs={"pic": ["f_abc", "f_def"]},
                declared=[_file_field()],
                file_service=_FakeFileService({}),
                user_id="u1",
            )

    @pytest.mark.asyncio
    async def test_unknown_file_raises(self) -> None:
        with pytest.raises(ToolExecutionError, match="was not found"):
            await materialize_file_inputs(
                inputs={"pic": "f_ghost"},
                declared=[_file_field()],
                file_service=_FakeFileService({}),
                user_id="u1",
            )

    @pytest.mark.asyncio
    async def test_foreign_file_raises(self) -> None:
        service = _FakeFileService({"f_abc": _meta(user_id="someone_else")})

        with pytest.raises(ToolExecutionError, match="does not belong"):
            await materialize_file_inputs(
                inputs={"pic": "f_abc"},
                declared=[_file_field()],
                file_service=service,
                user_id="u1",
            )

    @pytest.mark.asyncio
    async def test_mime_mismatch_raises(self) -> None:
        service = _FakeFileService({"f_abc": _meta(mime="application/pdf")})

        with pytest.raises(ToolExecutionError, match="not accepted"):
            await materialize_file_inputs(
                inputs={"pic": "f_abc"},
                declared=[_file_field(constraints={"accepted_mimes": ["image/*"]})],
                file_service=service,
                user_id="u1",
            )

    @pytest.mark.asyncio
    async def test_wildcard_mime_accepted(self) -> None:
        service = _FakeFileService({"f_abc": _meta()})

        result = await materialize_file_inputs(
            inputs={"pic": "f_abc"},
            declared=[_file_field(constraints={"accepted_mimes": ["image/*"]})],
            file_service=service,
            user_id="u1",
        )
        assert result["pic"]["file_id"] == "f_abc"

    @pytest.mark.asyncio
    async def test_missing_file_service_raises(self) -> None:
        with pytest.raises(ToolExecutionError, match="not configured"):
            await materialize_file_inputs(
                inputs={"pic": "f_abc"},
                declared=[_file_field()],
                file_service=None,
                user_id="u1",
            )

    @pytest.mark.asyncio
    async def test_missing_user_raises(self) -> None:
        with pytest.raises(ToolExecutionError, match="authenticated user"):
            await materialize_file_inputs(
                inputs={"pic": "f_abc"},
                declared=[_file_field()],
                file_service=_FakeFileService({}),
                user_id=None,
            )


# ----------------------------------------------------------------- engine


def _run_with_inputs(inputs: dict) -> WorkflowRun:
    now = now_utc_iso()
    return WorkflowRun(run_id="r1", created_at=now, updated_at=now, inputs=inputs)


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


_MATERIALIZED = {
    "file_id": "f_abc",
    "user_id": "u1",
    "name": "photo.png",
    "mime": "image/png",
    "size": 68,
}


class TestExecuteToolInputFileBindings:
    @pytest.mark.asyncio
    async def test_materialized_input_lands_in_files_kwargs(self) -> None:
        run = _run_with_inputs({"pic": _MATERIALIZED})
        step = WorkflowStep(
            id="sink",
            type="tool",
            command="@consumer --go",
            file_bindings={"source": "${inputs.pic}"},
        )

        result, captured = await _execute(run, step)

        assert result.ok
        assert captured["kwargs"]["files"] == [{"slot": "source", **_MATERIALIZED}]

    @pytest.mark.asyncio
    async def test_no_url_key_on_uploaded_file_wire(self) -> None:
        # A stale url must never ride along - the consuming tool re-mints it.
        run = _run_with_inputs({"pic": {**_MATERIALIZED, "url": "https://stale"}})
        step = WorkflowStep(
            id="sink",
            type="tool",
            command="@consumer --go",
            file_bindings={"source": "${inputs.pic}"},
        )

        result, captured = await _execute(run, step)

        assert result.ok
        (file,) = captured["kwargs"]["files"]
        assert "url" not in file

    @pytest.mark.asyncio
    async def test_missing_name_falls_back_to_slot(self) -> None:
        run = _run_with_inputs({"pic": {"file_id": "f_abc", "user_id": "u1"}})
        step = WorkflowStep(
            id="sink",
            type="tool",
            command="@consumer --go",
            file_bindings={"source": "${inputs.pic}"},
        )

        result, captured = await _execute(run, step)

        assert result.ok
        (file,) = captured["kwargs"]["files"]
        assert file["name"] == "source"

    @pytest.mark.asyncio
    async def test_raw_file_id_string_fails_shape_check(self) -> None:
        # An unmaterialized file_id (start-op validation bypassed) must not
        # silently pass - the strict files channel rejects it.
        run = _run_with_inputs({"pic": "f_abc"})
        step = WorkflowStep(
            id="sink",
            type="tool",
            command="@consumer --go",
            file_bindings={"source": "${inputs.pic}"},
        )

        result, captured = await _execute(run, step)

        assert not result.ok
        assert "did not resolve to an image or uploaded file" in result.error
        assert "kwargs" not in captured

    @pytest.mark.asyncio
    async def test_input_file_and_image_output_bindings_mix(self) -> None:
        run = _run_with_inputs({"pic": _MATERIALIZED})
        run.state["outputs"] = {"p": {"image": {"url": "data:image/png;base64,AA=="}}}
        step = WorkflowStep(
            id="sink",
            type="tool",
            command="@consumer --go",
            file_bindings={
                "source": "${outputs.p.image}",
                "reference": "${inputs.pic}",
            },
        )

        result, captured = await _execute(run, step)

        assert result.ok
        by_slot = {f["slot"]: f for f in captured["kwargs"]["files"]}
        assert by_slot["source"]["url"] == "data:image/png;base64,AA=="
        assert by_slot["reference"]["file_id"] == "f_abc"
