"""Tests for run report v2: the builder, redaction, and the router's sink."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.domains.agentverse.agents.orchestrator.report import (
    REPORT_VERSION,
    build_run_report,
    error_run_report,
)
from src.domains.agentverse.tools.tool_manager_v1.execution.envelope import error_envelope
from src.domains.agentverse.utils.redaction import (
    REDACTED,
    is_secret_key,
    strip_url_query,
    summarize_args,
    summarize_value,
)
from src.domains.agentverse.utils.run_trace import (
    STATUS_BLOCKED,
    STATUS_ERROR,
    STATUS_OK,
    STATUS_VETOED,
    TRACE_KEY,
    extract_children,
)

TOP_LEVEL_FIELDS = {
    "version",
    "run",
    "input",
    "output",
    "routing",
    "steps",
    "llm",
    "evaluation",
    "tool_reports",
    "cost",
    "timings",
    "flags",
    "errors",
}


def _realistic_ctx() -> dict:
    return {
        "run_id": "run-abc",
        "session_id": "sess-1",
        "_agent_id": "agent-9",
        "role": "general",
        "started_at": "2026-07-28T10:00:00+00:00",
        "ended_at": "2026-07-28T10:00:04+00:00",
        "request": "draw me a cat",
        "raw_request": "draw me a cat --size=1024x1024",
        "goal": "draw me a cat",
        "history": [{"role": "user", "content": "hi"}],
        "brief": {"intent": "image.generate", "acceptance": "one cat image"},
        "plan": {"steps": ["draw"]},
        "actions_needed": ["draw"],
        "selected_tool": "draw",
        "route_confidence": 0.95,
        "signals": {"confidence": 0.95},
        "artifact": {
            "kind": "text",
            "content": "Here is your cat.",
            "images": [{"url": "https://blob.example/cat.png", "size": "1024x1024"}],
        },
        "eval": {"score": 0.9, "verdict": "pass"},
        "reports": {"auditor": {"findings": []}},
        "metrics": {"analyze_ms": 120, "task_ms": 3300, "evaluate_ms": 40},
        "addons_log": [
            {
                "tool": "draw",
                "phase": "task",
                "addons": {
                    "wallet_events": [
                        {"kind": "expense", "currency": "USD", "amount_minor": 6},
                    ]
                },
            }
        ],
        TRACE_KEY: [
            {
                "index": 0,
                "tool": "draw",
                "phase": "task",
                "status": STATUS_OK,
                "latency_ms": 3200,
                "children": [],
            }
        ],
        "_llm_calls": [
            {
                "provider": "openai",
                "model": "gpt-4o",
                "tokens": {"prompt_tokens": 30, "completion_tokens": 12, "total_tokens": 42},
                "latency_ms": 900,
            }
        ],
        "flags": {"user": {"verbose": True}, "system": {"rapport": False}},
    }


# --------------------------------------------------------------------------
# Builder
# --------------------------------------------------------------------------


def test_realistic_ctx_populates_every_block():
    report = build_run_report(_realistic_ctx())

    assert set(report) == TOP_LEVEL_FIELDS
    assert report["version"] == REPORT_VERSION
    assert report["errors"] == []

    assert report["run"]["run_id"] == "run-abc"
    assert report["run"]["session_id"] == "sess-1"
    assert report["run"]["agent_id"] == "agent-9"

    assert report["routing"]["intent"] == "image.generate"
    assert report["routing"]["selected_tool"] == "draw"
    assert report["routing"]["confidence"] == 0.95

    assert len(report["steps"]) == 1
    assert report["steps"][0]["tool"] == "draw"

    assert report["llm"][0]["model"] == "gpt-4o"
    assert report["cost"]["llm_tokens"] == 42

    assert report["evaluation"]["verdict"] == "pass"
    assert report["tool_reports"]["auditor"] == {"findings": []}

    assert report["timings"]["by_phase"]["task_ms"] == 3300
    assert report["timings"]["total_ms"] == 3460


def test_run_duration_is_wall_clock_not_the_phase_sum():
    """`timings.total_ms` sums phases; `run.duration_ms` is the turn's real span."""
    report = build_run_report(_realistic_ctx())

    assert report["run"]["duration_ms"] == 4000
    assert report["timings"]["total_ms"] == 3460


def test_run_duration_is_null_without_usable_timestamps():
    assert build_run_report({"started_at": "not-a-timestamp"})["run"]["duration_ms"] is None
    assert build_run_report({})["run"]["duration_ms"] is None


def test_images_are_counted_even_under_a_text_artifact():
    """v1 gated image stats on artifact["kind"] == "images" and reported null."""
    report = build_run_report(_realistic_ctx())

    assert report["output"]["kind"] == "text"
    assert report["output"]["images"]["count"] == 1
    assert report["output"]["images"]["sizes"] == ["1024x1024"]


def test_cost_rolls_up_wallet_events_per_tool():
    report = build_run_report(_realistic_ctx())

    assert report["cost"]["total_cents"] == 6
    assert report["cost"]["by_tool"] == [{"tool": "draw", "cents": 6}]
    assert report["cost"]["currency"] == "USD"


def test_empty_ctx_still_returns_a_valid_report():
    report = build_run_report({})

    assert set(report) == TOP_LEVEL_FIELDS
    assert report["version"] == REPORT_VERSION
    assert report["errors"] == []
    assert report["steps"] == []
    assert report["run"]["run_id"] is None


def test_none_ctx_still_returns_a_valid_report():
    assert set(build_run_report(None)) == TOP_LEVEL_FIELDS


def test_error_report_keeps_the_full_v2_shape():
    report = error_run_report("RuntimeError: boom")

    assert set(report) == TOP_LEVEL_FIELDS
    assert report["version"] == REPORT_VERSION
    assert report["steps"] is None
    assert report["errors"] == [{"field": "report", "error": "RuntimeError: boom"}]


def test_a_field_that_raises_is_recorded_in_errors_and_left_null():
    class Exploding(dict):
        def get(self, key, default=None):
            if key == "artifact":
                raise RuntimeError("boom")
            return super().get(key, default)

    report = build_run_report(Exploding({"run_id": "run-1"}))

    assert report["output"] is None
    assert {"field": "output"} == {"field": report["errors"][0]["field"]}
    assert "boom" in report["errors"][0]["error"]
    # Every other block still built.
    assert report["run"]["run_id"] == "run-1"


def test_plan_steps_appear_exactly_once():
    """v1 shipped the same list as actions, plan_steps and tools.planned."""
    report = build_run_report(_realistic_ctx())

    flattened = str(report)
    assert flattened.count("'plan_steps'") == 1
    assert "actions" not in report["routing"]


# --------------------------------------------------------------------------
# Redaction
# --------------------------------------------------------------------------


def test_sas_query_string_is_stripped_from_urls():
    sas = "https://acct.blob.core.windows.net/c/f.png?sv=2024&sig=SECRETSIG&se=2026"

    assert strip_url_query(sas) == "https://acct.blob.core.windows.net/c/f.png"
    assert "sig=" not in str(summarize_value({"url": sas}))


def test_denylisted_keys_are_redacted():
    args = {
        "api_key": "sk-live-123",
        "access_token": "tok",
        "password": "hunter2",
        "sas_url": "https://x/y?sig=z",
        "Authorization": "Bearer abc",
        "prompt": "a cat",
    }
    out = summarize_args(args)

    assert out["api_key"] == REDACTED
    assert out["access_token"] == REDACTED
    assert out["password"] == REDACTED
    assert out["sas_url"] == REDACTED
    assert out["Authorization"] == REDACTED
    assert out["prompt"] == "a cat"


@pytest.mark.parametrize(
    "key",
    [
        "token",
        "refresh_token",
        "SECRET",
        "api_key",
        "apiKey",
        "key",
        "access_keys",
        "passwd",
        "sas",
        "sas_url",
        "sasUrl",
        "authorization",
        "client-secret",
    ],
)
def test_secret_key_detection_is_case_insensitive_and_word_based(key):
    assert is_secret_key(key)


@pytest.mark.parametrize(
    "key",
    ["author", "keywords", "key_points", "key_findings", "monkey", "authenticity", "sassy"],
)
def test_ordinary_words_containing_a_secret_substring_survive(key):
    """A report whose every field reads REDACTED is one nobody opens twice."""
    assert not is_secret_key(key)
    assert summarize_value({key: "visible"})[key] == "visible"


def test_nested_secrets_are_redacted_too():
    out = summarize_value({"outer": {"inner": {"client_secret": "shh", "name": "ok"}}})

    assert out["outer"]["inner"]["client_secret"] == REDACTED
    assert out["outer"]["inner"]["name"] == "ok"


def test_internal_plumbing_is_dropped_at_every_depth():
    """`h_task` passes the whole tool_kwargs bag again under `overrides.args`.

    A one-level filter at the call site would let the trace sink, the socket
    emitter and the agent ride into the persisted report nested one key deeper.
    """
    sink: list = []
    tool_kwargs = {"_tool_trace": sink, "_socket_emitter": object(), "prompt": "a cat"}
    kwargs = {
        "prompt": "a cat",
        "agent": object(),
        "history": [{"role": "user", "content": "hi"}],
        "tool_kwargs": tool_kwargs,
        "overrides": {"cli_flags": {}, "args": tool_kwargs},
    }

    out = summarize_args(kwargs)

    assert out == {"prompt": "a cat"}
    assert "_tool_trace" not in str(out)
    assert "_socket_emitter" not in str(out)


def test_args_never_embed_the_trace_sink_that_holds_them():
    """The sink grows while the tool runs; a step must not contain its siblings."""
    sink: list = [{"index": 0, "tool": "earlier_step"}]
    out = summarize_args({"overrides": {"args": {"_tool_trace": sink}}, "prompt": "a cat"})

    assert "earlier_step" not in str(out)


def test_long_strings_are_truncated_and_blobs_collapse_to_a_size():
    long_prose = "word " * 200
    assert len(summarize_value(long_prose)) <= 201

    blob = "A" * 5000
    assert summarize_value(blob) == {"type": "string", "bytes": 5000}
    assert summarize_value(b"\x00" * 12) == {"type": "bytes", "bytes": 12}


# --------------------------------------------------------------------------
# Router sink
# --------------------------------------------------------------------------


def _router_kwargs(sink: list, **extra):
    return {"text": "hello", "prompt": "a cat", TRACE_KEY: sink, **extra}


async def _run_router(sink: list, *, route="pure", veto=None, depth_err=None, response=None):
    """Drive execute_router with every collaborator stubbed out."""
    from src.domains.agentverse.tools.tool_manager_v1.execution.router import execute_router

    manager = MagicMock()
    manager.agent = MagicMock(id="agent-1", name="Eva")
    manager._factories = {"draw": {"config": None, "meta": {}}}
    manager._tool_anchors = {}

    async def _path(*args, **kw):
        if isinstance(response, Exception):
            raise response
        return response

    prep = MagicMock(
        inst=MagicMock(),
        params={},
        param_specs={},
        requires_collection=False,
        clean_text="hello",
        flag_overrides={},
        ctx={},
    )

    return await execute_router(
        MagicMock(),
        manager,
        key="draw",
        kwargs=_router_kwargs(sink),
        normalize_execute_inputs=lambda m, k, kw: (k, kw),
        pop_common_execute_kwargs=lambda kw: (
            {
                "text": kw.pop("text", ""),
                "history": None,
                "file": None,
                "extra": None,
                "session_id": None,
                "request": None,
                "policy": None,
            },
            kw,
        ),
        build_effective_context=lambda m, **kw: ("hello", {}, {}),
        apply_depth_guard=lambda ctx, key: depth_err,
        evaluate_policy=lambda *a, **kw: veto,
        route_for_key=lambda k: route,
        execute_system_minimal=_path,
        execute_invoker_path=_path,
        prepare_pure_prep=lambda *a, **kw: prep,
        execute_pure_path=_path,
        collect_param_specs=None,
        extract_cli_overrides=None,
        strip_tool_anchor=None,
        flag_re=None,
        resolve_param_name=None,
        strip_quotes=None,
        coerce_val=None,
        apply_config_defaults=None,
        get_cfg_value=None,
        maybe_tool_infer=None,
    )


@pytest.mark.asyncio
async def test_router_records_a_successful_execution():
    sink: list = []
    await _run_router(sink, response={"success": True, "result": {"answer": "a cat"}})

    assert len(sink) == 1
    step = sink[0]
    assert step["tool"] == "draw"
    assert step["route"] == "pure"
    assert step["status"] == STATUS_OK
    assert step["result"]["text_preview"] == "a cat"
    assert step["args"]["prompt"] == "a cat"
    assert step["error"] is None
    assert step["children"] == []


@pytest.mark.asyncio
async def test_router_records_a_failed_tool_execution():
    sink: list = []
    await _run_router(sink, response={"success": False, "error": "upstream exploded"})

    assert sink[0]["status"] == STATUS_ERROR
    assert sink[0]["result"]["error"] == "upstream exploded"


@pytest.mark.asyncio
async def test_router_records_a_raised_exception_and_reraises():
    sink: list = []
    with pytest.raises(RuntimeError):
        await _run_router(sink, response=RuntimeError("kaboom"))

    assert sink[0]["status"] == STATUS_ERROR
    assert "kaboom" in sink[0]["error"]


@pytest.mark.asyncio
async def test_router_records_a_policy_veto():
    """The gate tags its own envelope; the wrapper reads the status back off it."""
    sink: list = []
    await _run_router(sink, veto="tool not permitted for this agent")

    assert sink[0]["status"] == STATUS_VETOED
    assert sink[0]["result"]["error"] == "tool not permitted for this agent"


@pytest.mark.asyncio
async def test_router_records_a_depth_guard_block():
    sink: list = []
    await _run_router(sink, depth_err=error_envelope("Tool nesting too deep", kind=STATUS_BLOCKED))

    assert sink[0]["status"] == STATUS_BLOCKED


@pytest.mark.asyncio
async def test_router_never_leaks_the_sink_into_tool_kwargs():
    sink: list = []
    captured: dict = {}

    from src.domains.agentverse.tools.tool_manager_v1.execution.router import execute_router

    manager = MagicMock()
    manager.agent = MagicMock(id="a", name="n")
    manager._factories = {}
    manager._tool_anchors = {}

    async def _capture(*args, **kw):
        captured.update(kw)
        return {"success": True}

    await execute_router(
        MagicMock(),
        manager,
        key="chat",
        kwargs=_router_kwargs(sink),
        normalize_execute_inputs=lambda m, k, kw: (k, kw),
        pop_common_execute_kwargs=lambda kw: (
            {
                "text": kw.pop("text", ""),
                "history": None,
                "file": None,
                "extra": None,
                "session_id": None,
                "request": None,
                "policy": None,
            },
            kw,
        ),
        build_effective_context=lambda m, **kw: ("hello", {}, {}),
        apply_depth_guard=lambda ctx, key: None,
        evaluate_policy=lambda *a, **kw: None,
        route_for_key=lambda k: "llm_only",
        execute_system_minimal=_capture,
        execute_invoker_path=_capture,
        prepare_pure_prep=lambda *a, **kw: None,
        execute_pure_path=_capture,
        collect_param_specs=None,
        extract_cli_overrides=None,
        strip_tool_anchor=None,
        flag_re=None,
        resolve_param_name=None,
        strip_quotes=None,
        coerce_val=None,
        apply_config_defaults=None,
        get_cfg_value=None,
        maybe_tool_infer=None,
    )

    assert TRACE_KEY not in captured
    assert len(sink) == 1


@pytest.mark.asyncio
async def test_router_without_a_sink_is_a_no_op():
    """Tools called outside an orchestrated turn must still execute."""
    result = await _run_router(None, response={"success": True})  # type: ignore[arg-type]
    assert result == {"success": True}


# --------------------------------------------------------------------------
# Composite steps
# --------------------------------------------------------------------------


def test_workflow_sub_steps_are_lifted_into_children():
    env = {
        "success": True,
        "result": {
            "kind": "workflow.run",
            "meta": {
                "addons": {
                    "steps": [
                        {
                            "tool": "step_a",
                            "status": "SUCCEEDED",
                            "attempts": 1,
                            "started_at": "2026-07-28T10:00:00+00:00",
                            "ended_at": "2026-07-28T10:00:01+00:00",
                            "error": None,
                            "children": [],
                        }
                    ]
                }
            },
        },
    }

    children = extract_children(env)

    assert len(children) == 1
    assert children[0]["tool"] == "step_a"
    assert children[0]["status"] == "SUCCEEDED"


def test_a_plain_tool_reports_no_children():
    assert extract_children({"success": True, "result": {"answer": "hi"}}) == []
