"""Tests for the workflow run frontend contract.

Coverage:
- normalize_inputs_payload (DSL --inputs= parsing)
- _serialize_steps helper
- handle_get includes the canonical workflow run snapshot
- handle_start response includes status, steps, and sanitized outputs
- DSL @workflow --op=start --inputs=<urlencoded-json> produces the same
  inputs dict as the structured tool_kwargs path
- handle_resume returns the same canonical snapshot shape
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import quote

import pytest

# The fixtures below all execute as tool key `writer`. Importing its spec
# registers it, so `declared_outputs` resolves here the way it does in the real
# app, where the registry is populated at startup.
import src.domains.agentverse.tools.writer_tool.spec  # noqa: F401
from src.domains.agentverse.tools.base import ToolExecutionError
from src.domains.agentverse.tools.workflow_tool.event import make_on_event
from src.domains.agentverse.tools.workflow_tool.helpers.payload.inputs import (
    normalize_inputs_payload,
)
from src.domains.agentverse.tools.workflow_tool.helpers.response import (
    _serialize_steps,
)
from src.domains.agentverse.tools.workflow_tool.helpers.run_ops import (
    handle_get,
    handle_resume,
    handle_start,
)
from src.domains.agentverse.tools.workflow_tool.libs.engine.execute.pause import (
    execute_pause,
)
from src.domains.agentverse.tools.workflow_tool.models import (
    PauseState,
    StepRuntime,
    WorkflowDefinition,
    WorkflowRun,
    now_utc_iso,
)
from src.domains.agentverse.tools.workflow_tool.store import InMemoryRunStore

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_BATCH_STEPS = [
    {"id": "write", "type": "tool", "command": "@writer --text=${inputs.text}"},
    {"id": "end", "type": "end", "depends_on": ["write"]},
]

_PAUSE_STEPS = [
    {"id": "write", "type": "tool", "command": "@writer --text=${inputs.text}"},
    {
        "id": "review",
        "type": "pause",
        "depends_on": ["write"],
        "pause": {"prompt": "Review before continuing."},
    },
    {"id": "end", "type": "end", "depends_on": ["review"]},
]


def _batch_wf() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {"execution_mode": "sequential", "steps": _BATCH_STEPS}
    )


def _pause_wf() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {"execution_mode": "sequential", "steps": _PAUSE_STEPS}
    )


def _running_run(run_id: str = "run1") -> WorkflowRun:
    now = now_utc_iso()
    return WorkflowRun(
        run_id=run_id,
        workflow_name="Test",
        created_at=now,
        updated_at=now,
        status="RUNNING",
        inputs={"text": "hello"},
        state={
            "_workflow": {"key": "test_wf", "name": "Test"},
            # Binding namespace: clean projected keys.
            "outputs": {"write": {"answer": "Persisted text"}},
            # Display namespace: the raw {tool, env} wrapper the snapshot renders.
            "display_outputs": {
                "write": {
                    "tool": "writer",
                    "env": {"answer": "Persisted text"},
                }
            },
        },
        steps={
            "write": StepRuntime(status="COMPLETED", attempts=1),
        },
    )


def _paused_run(run_id: str = "run_pause") -> WorkflowRun:
    now = now_utc_iso()
    run = WorkflowRun(
        run_id=run_id,
        workflow_name="Test Pause Workflow",
        created_at=now,
        updated_at=now,
        status="PAUSED",
        inputs={"text": "hello"},
        state={"_workflow": {"key": "test_pause_wf", "name": "Test Pause Workflow"}},
        steps={
            "write": StepRuntime(status="COMPLETED", attempts=1),
            "review": StepRuntime(status="PAUSED", attempts=0),
        },
        paused={
            "review": PauseState(
                pause_id="run_pause:review",
                step_id="review",
                prompt="Review before continuing.",
                actions=["continue", "cancel"],
            )
        },
    )
    return run


def _assert_no_private_output_keys(value: Any) -> None:
    if isinstance(value, dict):
        private = {"env", "raw", "raw_payload", "payload", "debug", "trace"}
        assert private.isdisjoint(value.keys())
        for item in value.values():
            _assert_no_private_output_keys(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_private_output_keys(item)


def _assert_display_only_output(output: dict[str, Any]) -> None:
    """Each snapshot/step output is the slim display-only shape."""
    assert set(output) == {"ok", "tool", "text", "images", "error", "declared_outputs"}
    assert isinstance(output["images"], list)
    for img in output["images"]:
        assert set(img) <= {"url", "mime_type"}
    _assert_declared_output_annotation(output)


def _assert_declared_output_annotation(output: dict[str, Any]) -> None:
    """`declared_outputs` names the tool's declared keys without re-sending images.

    An image key carries indexes into this payload's own `images` array; every
    other key carries its value inline.
    """
    declared = output["declared_outputs"]
    assert isinstance(declared, dict)

    for key, entry in declared.items():
        assert isinstance(key, str) and key
        assert isinstance(entry["label"], str)
        assert isinstance(entry["type"], str)

        if entry["type"] == "image":
            assert set(entry) == {"label", "type", "image_indexes"}
            # Referenced, never repeated - the payload carries each image once.
            assert "value" not in entry
            for i in entry["image_indexes"]:
                assert 0 <= i < len(output["images"])
        else:
            assert set(entry) == {"label", "type", "value"}


def _assert_canonical_run_snapshot(result: dict[str, Any], *, expect_meta: bool = True) -> None:
    keys = {
        "kind",
        "status",
        "run_id",
        "workflow_key",
        "workflow_name",
        "paused",
        "error",
        "steps",
        "outputs",
    }
    # Run-report telemetry: the per-step runtime the trace lifts into the step
    # record's children. Only a run that executed carries it - a plain `get`
    # and the socket snapshot stay on the bare contract.
    if expect_meta:
        keys.add("meta")

    assert set(result) == keys
    assert result["kind"] == "workflow.run"
    assert isinstance(result["steps"], dict)
    assert isinstance(result["outputs"], dict)
    if expect_meta:
        assert isinstance(result["meta"]["addons"]["steps"], list)


async def _mock_engine_call(
    *,
    engine: Any,
    store: Any,
    wf: WorkflowDefinition,
    run: WorkflowRun,
    tool_runner: Any,
    context: Any,
    on_step_event: Any = None,
) -> tuple[WorkflowRun, dict[str, Any]]:
    """Fake engine: mark the write step COMPLETED and set run to COMPLETED."""
    run.steps["write"] = StepRuntime(status="COMPLETED", attempts=1)
    run.status = "COMPLETED"
    await store.put(run)
    outputs = {
        "write": {
            "ok": True,
            "tool": "writer",
            "message": "Written text",
            "text": "Written text",
            "renderables": [{"kind": "markdown", "text": "Written text"}],
            "result": {"answer": "Written text"},
            "error": None,
        }
    }
    return run, outputs


async def _mock_cancel_engine_call(
    *,
    engine: Any,
    store: Any,
    wf: WorkflowDefinition,
    run: WorkflowRun,
    tool_runner: Any,
    context: Any,
    on_step_event: Any = None,
) -> tuple[WorkflowRun, dict[str, Any]]:
    run.status = "CANCELLED"
    await store.put(run)
    return run, {}


# ---------------------------------------------------------------------------
# Unit tests: normalize_inputs_payload
# ---------------------------------------------------------------------------


class TestNormalizeInputsPayload:
    def test_dict_passthrough(self) -> None:
        data = {"text": "hello", "count": 3}
        assert normalize_inputs_payload(data) == data

    def test_none_returns_empty(self) -> None:
        assert normalize_inputs_payload(None) == {}

    def test_empty_string_returns_empty(self) -> None:
        assert normalize_inputs_payload("") == {}

    def test_plain_json_string(self) -> None:
        raw = json.dumps({"text": "hello"})
        assert normalize_inputs_payload(raw) == {"text": "hello"}

    def test_urlencoded_json_string(self) -> None:
        raw = quote(json.dumps({"text": "hello world", "n": 5}))
        result = normalize_inputs_payload(raw)
        assert result == {"text": "hello world", "n": 5}

    def test_invalid_json_raises(self) -> None:
        with pytest.raises(ToolExecutionError, match="Invalid inputs JSON"):
            normalize_inputs_payload("not-json")

    def test_non_object_json_raises(self) -> None:
        with pytest.raises(ToolExecutionError, match="must decode to an object"):
            normalize_inputs_payload(json.dumps([1, 2, 3]))

    def test_wrong_type_raises(self) -> None:
        with pytest.raises(ToolExecutionError, match="must be dict or JSON string"):
            normalize_inputs_payload(42)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Unit tests: _serialize_steps
# ---------------------------------------------------------------------------


class TestSerializeSteps:
    def test_empty_steps(self) -> None:
        run = _running_run()
        run.steps = {}
        assert _serialize_steps(run) == {}

    def test_populated_steps(self) -> None:
        run = _running_run()
        result = _serialize_steps(run)

        assert "write" in result
        assert result["write"]["status"] == "COMPLETED"
        assert result["write"]["attempts"] == 1
        assert "started_at" in result["write"]
        assert "last_error" in result["write"]


# ---------------------------------------------------------------------------
# handle_get: includes steps snapshot
# ---------------------------------------------------------------------------


class TestHandleGet:
    @pytest.mark.asyncio
    async def test_get_returns_steps(self) -> None:
        store = InMemoryRunStore()
        run = _running_run()
        await store.put(run)

        result = await handle_get(store=store, run_id="run1")

        _assert_canonical_run_snapshot(result, expect_meta=False)
        assert result["run_id"] == "run1"
        assert result["status"] == "RUNNING"
        assert result["steps"]["write"]["status"] == "COMPLETED"
        assert result["outputs"]["write"]["text"] == "Persisted text"
        # Reloaded from persisted state, the snapshot still names the declared
        # key, so a page reload can re-link ${outputs.write.answer} to this value.
        assert result["outputs"]["write"]["declared_outputs"] == {
            "answer": {"label": "Answer", "type": "string", "value": "Persisted text"}
        }
        _assert_display_only_output(result["outputs"]["write"])
        _assert_no_private_output_keys(result["outputs"])

    @pytest.mark.asyncio
    async def test_get_omits_legacy_duplicate_fields(self) -> None:
        store = InMemoryRunStore()
        run = _running_run()
        await store.put(run)

        result = await handle_get(store=store, run_id="run1")

        for field in ("message", "text", "plan", "renderables", "result", "output_keys"):
            assert field not in result

    @pytest.mark.asyncio
    async def test_get_derives_workflow_key_and_name_from_run_state(self) -> None:
        store = InMemoryRunStore()
        run = _running_run()
        await store.put(run)

        result = await handle_get(store=store, run_id="run1")

        _assert_canonical_run_snapshot(result, expect_meta=False)
        assert result["workflow_key"] == "test_wf"
        assert result["workflow_name"] == "Test"

    @pytest.mark.asyncio
    async def test_get_missing_run_returns_not_found_snapshot(self) -> None:
        store = InMemoryRunStore()
        result = await handle_get(store=store, run_id="nonexistent")

        assert result["kind"] == "workflow.run"
        assert result["status"] == "NOT_FOUND"
        assert result["run_id"] == "nonexistent"
        assert result["error"] is not None
        assert result["workflow_key"] is None
        assert result["workflow_name"] is None
        assert result["paused"] is None
        assert result["steps"] == {}
        assert result["outputs"] == {}

    @pytest.mark.asyncio
    async def test_get_no_run_id_raises(self) -> None:
        store = InMemoryRunStore()
        with pytest.raises(ToolExecutionError, match="run_id is required"):
            await handle_get(store=store, run_id=None)


# ---------------------------------------------------------------------------
# handle_start: canonical response contract
# ---------------------------------------------------------------------------


class TestHandleStartResponseContract:
    def _new_run(
        self,
        *,
        wf: WorkflowDefinition,
        run_id: str,
        inputs: dict[str, Any] | None,
        workflow_key: str | None = None,
        workflow_name: str | None = None,
    ) -> WorkflowRun:
        now = now_utc_iso()
        return WorkflowRun(
            run_id=run_id,
            workflow_name=workflow_name,
            created_at=now,
            updated_at=now,
            status="RUNNING",
            inputs=inputs or {},
            state={"_workflow": {"key": workflow_key, "name": workflow_name}},
        )

    @pytest.mark.asyncio
    async def test_start_response_includes_steps_and_sanitized_outputs(self) -> None:
        store = InMemoryRunStore()
        wf = _batch_wf()
        engine = MagicMock()

        result = await handle_start(
            engine_call=_mock_engine_call,
            new_run=self._new_run,
            store=store,
            engine=engine,
            wf=wf,
            workflow_key="test_wf",
            workflow_name="Test Batch Workflow",
            run_id="run_start_1",
            inputs={"text": "hello"},
            context={},
            tool_runner=AsyncMock(),
        )

        _assert_canonical_run_snapshot(result)
        assert "write" in result["steps"]
        assert result["outputs"]["write"]["text"] == "Written text"
        # This fixture returns a pre-presented output with no `env`, so the tool
        # name falls back to the step id and no declared outputs resolve. The
        # annotation degrades to {} rather than failing the step.
        assert result["outputs"]["write"]["declared_outputs"] == {}
        _assert_display_only_output(result["outputs"]["write"])
        _assert_no_private_output_keys(result["outputs"])

    @pytest.mark.asyncio
    async def test_start_response_omits_legacy_duplicate_fields(self) -> None:
        store = InMemoryRunStore()
        wf = _batch_wf()
        engine = MagicMock()

        result = await handle_start(
            engine_call=_mock_engine_call,
            new_run=self._new_run,
            store=store,
            engine=engine,
            wf=wf,
            workflow_key="test_wf",
            workflow_name="Test Batch Workflow",
            run_id="run_start_2",
            inputs={"text": "hello"},
            context={},
            tool_runner=AsyncMock(),
        )

        for field in ("message", "text", "plan", "renderables", "result", "output_keys"):
            assert field not in result

    @pytest.mark.asyncio
    async def test_start_inputs_carried_in_run(self) -> None:
        """inputs passed to handle_start appear on the created run."""
        store = InMemoryRunStore()
        wf = _batch_wf()
        engine = MagicMock()

        await handle_start(
            engine_call=_mock_engine_call,
            new_run=self._new_run,
            store=store,
            engine=engine,
            wf=wf,
            workflow_key="test_wf",
            run_id="run_start_3",
            inputs={"text": "my text"},
            context={},
            tool_runner=AsyncMock(),
        )

        run = await store.get("run_start_3")
        assert run is not None
        assert run.inputs == {"text": "my text"}


# ---------------------------------------------------------------------------
# DSL --inputs= parsing: extract_flag_from_text integration
# ---------------------------------------------------------------------------


class TestDslInputsParsing:
    def test_extract_from_raw_text(self) -> None:
        """extract_flag_from_text + normalize_inputs_payload mimics the DSL path."""
        from src.domains.agentverse.tools.workflow_tool.helpers.payload.pause import (
            extract_flag_from_text,
        )

        inputs_dict = {"text": "hello world", "count": 2}
        raw_text = f"@workflow --op=start --inputs={quote(json.dumps(inputs_dict))}"

        extracted = extract_flag_from_text(raw_text, "inputs")
        assert extracted is not None
        result = normalize_inputs_payload(extracted)
        assert result == inputs_dict

    def test_dsl_and_structured_paths_produce_same_result(self) -> None:
        """Both the DSL path and the structured dict path yield the same inputs."""
        from src.domains.agentverse.tools.workflow_tool.helpers.payload.pause import (
            extract_flag_from_text,
        )

        inputs_dict = {"text": "test value", "flag": True}

        # Structured path: already a dict
        structured = normalize_inputs_payload(inputs_dict)

        # DSL path: urlencoded JSON extracted from text
        raw_text = f"@workflow --op=start --inputs={quote(json.dumps(inputs_dict))}"
        extracted = extract_flag_from_text(raw_text, "inputs")
        dsl = normalize_inputs_payload(extracted)

        assert structured == dsl


# ---------------------------------------------------------------------------
# handle_resume: canonical response contract
# ---------------------------------------------------------------------------


class TestHandleResumeResponseContract:
    @pytest.mark.asyncio
    async def test_resume_cancel_response_uses_canonical_snapshot(self) -> None:
        store = InMemoryRunStore()
        wf = _pause_wf()
        run = _paused_run()
        await store.put(run)

        engine = MagicMock()

        async def _fake_cancel_pause(
            *,
            run: WorkflowRun,
            step_id: str,
            payload: Any,
            store: Any,
            on_step_event: Any = None,
        ) -> WorkflowRun:
            run.status = "CANCELLED"
            run.paused = {}
            await store.put(run)
            return run

        engine.cancel_pause = _fake_cancel_pause

        result = await handle_resume(
            engine_call=_mock_cancel_engine_call,
            store=store,
            engine=engine,
            wf=wf,
            run_id="run_pause",
            pause_payload={"action": "cancel", "step_id": "review"},
            context={},
            tool_runner=AsyncMock(),
        )

        _assert_canonical_run_snapshot(result)
        assert result["status"] == "CANCELLED"

    @pytest.mark.asyncio
    async def test_resume_continue_response_uses_canonical_snapshot(self) -> None:
        store = InMemoryRunStore()
        wf = _pause_wf()
        run = _paused_run(run_id="run_pause_c")
        await store.put(run)

        engine = MagicMock()

        async def _fake_resume_step(
            *,
            run: WorkflowRun,
            step_id: str,
            payload: Any,
            store: Any,
            on_step_event: Any = None,
        ) -> WorkflowRun:
            run.paused = {}
            run.status = "RUNNING"
            await store.put(run)
            return run

        engine.resume_step = _fake_resume_step

        result = await handle_resume(
            engine_call=_mock_engine_call,
            store=store,
            engine=engine,
            wf=wf,
            run_id="run_pause_c",
            pause_payload={"action": "continue", "step_id": "review"},
            context={},
            tool_runner=AsyncMock(),
        )

        _assert_canonical_run_snapshot(result)
        assert result["outputs"]["write"]["text"] == "Written text"
        _assert_display_only_output(result["outputs"]["write"])
        _assert_no_private_output_keys(result["outputs"])


class _FakeSocketEmitter:
    """Mirrors the real ``SocketEventEmitter.emit(event: str, payload: dict)``
    two-arg signature, recording the payload contract dict for assertions."""

    def __init__(self) -> None:
        self.emitted: list[dict[str, Any]] = []
        self.events: list[str] = []

    async def emit(self, event: str, payload: dict[str, Any]) -> None:
        self.events.append(event)
        self.emitted.append(payload)


class TestWorkflowStepEventContract:
    @pytest.mark.asyncio
    async def test_tool_step_event_uses_minimal_status_and_sanitized_output(self) -> None:
        emitter = _FakeSocketEmitter()
        on_event = make_on_event(socket_emitter=emitter)  # type: ignore[arg-type]

        await on_event(
            {
                "type": "workflow.step",
                "step_type": "tool",
                "run_id": "run1",
                "workflow_key": "test_wf",
                "step_id": "write",
                "tool": "writer",
                "payload": {
                    "tool": "writer",
                    "env": {"answer": "Written text"},
                    "raw_payload": {"secret": "internal"},
                },
            }
        )

        assert len(emitter.emitted) == 1
        event = emitter.emitted[0]
        assert event["kind"] == "workflow.step"
        assert event["run_id"] == "run1"
        assert event["workflow_key"] == "test_wf"
        assert event["step_id"] == "write"
        assert event["status"] == "COMPLETED"
        assert event["output"]["text"] == "Written text"
        assert event["output"]["declared_outputs"] == {
            "answer": {"label": "Answer", "type": "string", "value": "Written text"}
        }
        _assert_display_only_output(event["output"])
        assert "message" not in event
        assert "tool" not in event
        assert "stepId" not in event
        _assert_no_private_output_keys(event["output"])

    @pytest.mark.asyncio
    async def test_non_workflow_step_event_is_not_forwarded(self) -> None:
        emitter = _FakeSocketEmitter()
        on_event = make_on_event(socket_emitter=emitter)  # type: ignore[arg-type]

        await on_event({"type": "workflow.debug", "run_id": "run1"})

        assert emitter.emitted == []


class TestEngineLiveStepEvents:
    @pytest.mark.asyncio
    async def test_engine_emits_running_before_tool_runner_starts(self) -> None:
        from src.domains.agentverse.tools.workflow_tool.engine import WorkflowEngine

        emitter = _FakeSocketEmitter()
        on_step_event = make_on_event(socket_emitter=emitter)  # type: ignore[arg-type]
        timeline: list[str] = []

        async def _tool_runner(tool_key: str, kwargs: dict[str, Any]) -> tuple[bool, Any]:
            assert emitter.emitted
            assert emitter.emitted[0]["step_id"] == "write"
            assert emitter.emitted[0]["status"] == "RUNNING"
            timeline.append("tool_runner_started")
            return True, {"success": True, "answer": "Written text"}

        wf = _batch_wf()
        run = WorkflowRun(
            run_id="wf_live_1",
            workflow_name="Test Batch Workflow",
            created_at=now_utc_iso(),
            updated_at=now_utc_iso(),
            status="RUNNING",
            inputs={"text": "hello"},
            state={"_workflow": {"key": "test_wf", "name": "Test Batch Workflow"}},
        )

        engine = WorkflowEngine()
        await engine.run_until_pause_or_done(
            run=run,
            wf=wf,
            tool_runner=_tool_runner,
            store=InMemoryRunStore(),
            on_step_event=on_step_event,
        )

        emitted = emitter.emitted
        assert timeline == ["tool_runner_started"]

        # Every emitted frame uses the canonical discriminator + identifiers.
        for frame in emitted:
            assert frame["kind"] == "workflow.step"
            assert frame["run_id"] == "wf_live_1"
            assert frame["workflow_key"] == "test_wf"
            assert frame["step_id"]
            assert frame["status"] in {
                "RUNNING",
                "PAUSED",
                "COMPLETED",
                "FAILED",
                "CANCELLED",
            }

        by_step: dict[str, list[str]] = {}
        for frame in emitted:
            by_step.setdefault(frame["step_id"], []).append(frame["status"])

        # The tool step must visibly sit in RUNNING before reaching COMPLETED.
        assert by_step["write"][0] == "RUNNING"
        assert "COMPLETED" in by_step["write"]
        assert by_step["write"].index("RUNNING") < by_step["write"].index("COMPLETED")

        # RUNNING frames carry no output; the terminal COMPLETED frame does.
        running_frames = [f for f in emitted if f["status"] == "RUNNING"]
        assert running_frames
        assert all("output" not in f for f in running_frames)

        write_completed = next(
            f for f in emitted if f["step_id"] == "write" and f["status"] == "COMPLETED"
        )
        assert write_completed["output"]["text"] == "Written text"
        _assert_display_only_output(write_completed["output"])
        _assert_no_private_output_keys({"write": write_completed["output"]})

    @pytest.mark.asyncio
    async def test_resume_step_emits_completed_for_pause_step(self) -> None:
        from src.domains.agentverse.tools.workflow_tool.engine import WorkflowEngine

        emitter = _FakeSocketEmitter()
        on_step_event = make_on_event(socket_emitter=emitter)  # type: ignore[arg-type]

        run = _paused_run("wf_resume_1")
        run.state = {
            "_workflow": {"key": "test_pause_wf", "name": "Test Pause Workflow"},
        }

        engine = WorkflowEngine()
        await engine.resume_step(
            run=run,
            step_id="review",
            payload={"action": "continue"},
            store=InMemoryRunStore(),
            on_step_event=on_step_event,
        )

        events = emitter.emitted
        assert len(events) == 1
        event = events[0]
        assert event["kind"] == "workflow.step"
        assert event["run_id"] == "wf_resume_1"
        assert event["step_id"] == "review"
        assert event["status"] == "COMPLETED"
        assert "output" not in event

    @pytest.mark.asyncio
    async def test_cancel_pause_emits_cancelled_for_pause_step(self) -> None:
        from src.domains.agentverse.tools.workflow_tool.engine import WorkflowEngine

        emitter = _FakeSocketEmitter()
        on_step_event = make_on_event(socket_emitter=emitter)  # type: ignore[arg-type]

        run = _paused_run("wf_cancel_1")
        run.state = {
            "_workflow": {"key": "test_pause_wf", "name": "Test Pause Workflow"},
        }

        engine = WorkflowEngine()
        await engine.cancel_pause(
            run=run,
            step_id="review",
            payload={"action": "cancel"},
            store=InMemoryRunStore(),
            on_step_event=on_step_event,
        )

        events = emitter.emitted
        assert len(events) == 1
        event = events[0]
        assert event["kind"] == "workflow.step"
        assert event["run_id"] == "wf_cancel_1"
        assert event["step_id"] == "review"
        assert event["status"] == "CANCELLED"
        assert "output" not in event


class TestResumeEmitsRunningSnapshot:
    @pytest.mark.asyncio
    async def test_running_snapshot_emitted_before_remaining_steps(self) -> None:
        from src.domains.agentverse.tools.workflow_tool.engine import WorkflowEngine
        from src.domains.agentverse.tools.workflow_tool.event import make_run_snapshot_emitter

        emitter = _FakeSocketEmitter()
        on_event = make_on_event(socket_emitter=emitter)  # type: ignore[arg-type]
        emit_run_snapshot = make_run_snapshot_emitter(socket_emitter=emitter)  # type: ignore[arg-type]

        store = InMemoryRunStore()
        run = _paused_run("wf_snap_1")
        run.state = {"_workflow": {"key": "test_pause_wf", "name": "Test Pause Workflow"}}
        await store.put(run)

        wf = _pause_wf()

        async def _noop_engine_call(
            *,
            engine: Any,
            store: Any,
            wf: Any,
            run: Any,
            tool_runner: Any,
            context: Any,
            on_step_event: Any = None,
        ) -> tuple[Any, dict[str, Any]]:
            return run, {}

        engine = WorkflowEngine()

        result = await handle_resume(
            engine_call=_noop_engine_call,
            store=store,
            engine=engine,
            wf=wf,
            run_id="wf_snap_1",
            pause_payload={"action": "continue", "step_id": "review"},
            context={},
            tool_runner=AsyncMock(),
            emit_step_event=on_event,
            emit_run_snapshot=emit_run_snapshot,
        )

        # Ordering: step COMPLETED event (from resume_step) comes first,
        # then the RUNNING run snapshot, then the final snapshot is returned.
        step_events = [e for e in emitter.emitted if e.get("kind") == "workflow.step"]
        run_snapshots = [e for e in emitter.emitted if e.get("kind") == "workflow.run"]

        # At least one RUNNING snapshot was emitted over the socket.
        assert run_snapshots, "expected at least one workflow.run snapshot to be emitted"
        running_snapshot = run_snapshots[0]
        assert running_snapshot["run_id"] == "wf_snap_1"
        assert running_snapshot["status"] == "RUNNING"
        assert running_snapshot["paused"] is None

        # The intermediate RUNNING snapshot is emitted after the step COMPLETED event.
        first_step_idx = emitter.emitted.index(step_events[0])
        first_snap_idx = emitter.emitted.index(running_snapshot)
        assert first_snap_idx > first_step_idx

        # The final snapshot returned by handle_resume is also non-paused.
        assert result["kind"] == "workflow.run"
        assert result["status"] in {"RUNNING", "COMPLETED", "FAILED"}
        assert result["paused"] is None


class TestExecutePauseContract:
    @pytest.mark.asyncio
    async def test_pause_emits_approve_reject_actions(self) -> None:
        """Pauses are a simple approve / reject gate: the emitted PauseState
        advertises the continue/cancel actions and carries no author form schema."""
        wf = _pause_wf()
        review_step = next(step for step in wf.steps if step.id == "review")
        run = _running_run()

        result = await execute_pause(run, review_step)

        assert result.status == "PAUSED"
        assert result.pause is not None
        assert result.pause.actions == ["continue", "cancel"]
        assert not hasattr(result.pause, "expects")


class TestFailedToolOutputError:
    """A failed tool step must surface its error in the display-only output."""

    def test_env_error_is_folded_into_output(self) -> None:
        from src.domains.agentverse.tools.workflow_tool.libs.engine.execute.tool import (
            _extract_error,
            _failed_tool_result,
        )
        from src.domains.agentverse.tools.workflow_tool.public_contract import output_display

        step = MagicMock()
        step.id = "write"

        # The runner contract is a flat env, so the error rides on the env itself.
        env = {"answer": "partial", "error": "boom", "success": False}
        error = _extract_error(env)
        assert error == "boom"

        result = _failed_tool_result(step=step, tool_key="writer", env=env, error=error)

        display = output_display("write", result.output)
        _assert_display_only_output(display)
        assert display["error"] == "boom"
        assert display["ok"] is False

    def test_generic_failure_still_reports_error_and_not_ok(self) -> None:
        from src.domains.agentverse.tools.workflow_tool.libs.engine.execute.tool import (
            _failed_tool_result,
        )
        from src.domains.agentverse.tools.workflow_tool.public_contract import output_display

        step = MagicMock()
        step.id = "write"

        # No error anywhere and an env that would otherwise render ok=true.
        result = _failed_tool_result(step=step, tool_key="writer", env={"answer": "x"}, error=None)

        display = output_display("write", result.output)
        _assert_display_only_output(display)
        assert display["error"] == "Tool failed: write"
        assert display["ok"] is False
