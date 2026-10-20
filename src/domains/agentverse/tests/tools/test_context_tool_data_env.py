"""Tests that a context tool's `data` survives the format phase into the env.

A `context_only` tool registers with `route: "invoker"`, so its step result is
built by the LLM path (`format_step` -> `invoker.llm` -> `as_envelope`) rather
than by `MCPProxyTool.execute`/`ToolEnv.as_env`. Both must hand the workflow
engine the same flat env shape, or a declared output `path` of `data.<field>`
resolves in pure mode and binds `None` in context mode.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.domains.agentverse.llm import invoker as invoker_mod
from src.domains.agentverse.llm.helpers.format_phase import format_step
from src.domains.agentverse.llm.invoker import llm
from src.domains.agentverse.tools.spec import ToolOutputSpec
from src.domains.agentverse.tools.tool_manager_v1.execution import invoker_path as invoker_path_mod
from src.domains.agentverse.tools.workflow_tool.helpers.declared_outputs import project_outputs

_DETECT_OUTPUTS = [
    ToolOutputSpec(key="answer", label="Answer", type="string", path="answer"),
    ToolOutputSpec(key="detections", label="Detections", type="list", path="data.detections"),
    ToolOutputSpec(key="count", label="Count", type="number", path="data.count"),
]

_DETECTIONS = [{"box_2d": [0, 0, 1, 1]}]


class _FormattingTool:
    """A context tool whose `format_result` returns the proxy payload shape."""

    key = "vision_bbox_detect"

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    async def format_result(self, llm_output: str) -> dict[str, Any]:
        return {"result": self._payload}


class TestFormatStep:
    @pytest.mark.asyncio
    async def test_data_is_carried_alongside_the_answer(self) -> None:
        tool = _FormattingTool(
            {
                "answer": "Detected 2 object(s).",
                "addons": {},
                "data": {"detections": _DETECTIONS, "count": 2},
            }
        )

        env = await format_step(tools=[tool], raw_text='[{"box_2d": [0, 0, 1, 1]}]')

        assert env.answer == "Detected 2 object(s)."
        assert env.data == {"detections": _DETECTIONS, "count": 2}

    @pytest.mark.asyncio
    async def test_a_tool_returning_no_data_leaves_it_unset(self) -> None:
        tool = _FormattingTool({"answer": "plain", "addons": {}})

        env = await format_step(tools=[tool], raw_text="plain")

        assert env.data is None
        assert "data" not in env.as_env()

    @pytest.mark.asyncio
    async def test_an_empty_data_dict_still_reaches_the_env(self) -> None:
        # `as_env` keys on `is not None`, so an explicit empty payload is a real
        # shape in both modes rather than one the context path silently drops.
        tool = _FormattingTool({"answer": "none found", "addons": {}, "data": {}})

        env = await format_step(tools=[tool], raw_text="none found")

        assert env.as_env()["data"] == {}

    @pytest.mark.asyncio
    async def test_addons_survive_an_answerless_payload(self) -> None:
        tool = _FormattingTool({"addons": {"error": "remote said no"}})

        env = await format_step(tools=[tool], raw_text="model said this")

        assert env.answer == "model said this"
        assert env.addons == {"error": "remote said no"}

    @pytest.mark.asyncio
    async def test_a_raising_formatter_leaves_the_text_intact(self) -> None:
        class _Boom:
            key = "boom"

            async def format_result(self, llm_output: str) -> dict[str, Any]:
                raise RuntimeError("remote down")

        env = await format_step(tools=[_Boom()], raw_text="model said this")

        assert env.answer == "model said this"
        assert env.data is None

    @pytest.mark.asyncio
    async def test_a_turn_with_no_formatting_tool_is_a_passthrough(self) -> None:
        env = await format_step(tools=[object()], raw_text="model said this")

        assert env.answer == "model said this"
        assert env.data is None


class TestInvokerResponse:
    """`llm()` itself must emit the formatted env, not just carry the answer."""

    @pytest.mark.asyncio
    async def test_data_reaches_the_llm_response(self, monkeypatch) -> None:
        # Persona resolution needs the profile registry and has no bearing on the
        # format phase, so it is stubbed rather than stood up.
        monkeypatch.setattr(invoker_mod, "resolve_personality", lambda *a, **k: object())

        tool = _FormattingTool(
            {
                "answer": "Detected 2 object(s).",
                "addons": {"note": "kept"},
                "data": {"detections": _DETECTIONS, "count": 2},
            }
        )

        async def _provide_context(**_kwargs: Any) -> dict[str, Any]:
            return {"system_instruction": "look for cats"}

        tool.provide_context = _provide_context  # type: ignore[attr-defined]

        class _Agent:
            personality_key = None

            def build_system_prompt_from_soul(self, *, soul: Any) -> dict[str, str]:
                return {"role": "system", "content": "you are a detector"}

            async def llm_chat_async(self, *, messages: Any, **_params: Any) -> str:
                return '[{"box_2d": [0, 0, 1, 1]}]'

        class _Mgr:
            def ensure_instance(self, key: str) -> Any:
                return tool

        response = await llm(
            agent=_Agent(),
            user_text="@vision_bbox_detect find the cats",
            providers=["vision_bbox_detect"],
            tool_manager=_Mgr(),
        )

        assert response["answer"] == "Detected 2 object(s)."
        assert response["addons"] == {"note": "kept"}
        assert response["data"] == {"detections": _DETECTIONS, "count": 2}


class TestInvokerPathEnv:
    """`execute_invoker_path`'s envelope carries the env declared outputs resolve on."""

    async def _envelope(self, monkeypatch, resp: dict[str, Any]) -> dict[str, Any]:
        async def _fake_llm(**_kwargs: Any) -> dict[str, Any]:
            return resp

        monkeypatch.setattr(invoker_path_mod, "llm", _fake_llm)

        class _Mgr:
            agent = object()

        return await invoker_path_mod.execute_invoker_path(
            _Mgr(),
            key="vision_bbox_detect",
            meta={},
            text="@vision_bbox_detect find the cats",
            history=[],
            session_id="s1",
            policy=None,
            effective_ctx={},
        )

    @pytest.mark.asyncio
    async def test_declared_data_paths_resolve(self, monkeypatch) -> None:
        envelope = await self._envelope(
            monkeypatch,
            {
                "answer_raw": "raw",
                "answer": "Detected 2 object(s).",
                "context": {},
                "tool_results": {},
                "addons": {},
                "data": {"detections": _DETECTIONS, "count": 2},
            },
        )

        projected = project_outputs(envelope["result"], _DETECT_OUTPUTS)

        assert projected["answer"] == "Detected 2 object(s)."
        assert projected["detections"] == _DETECTIONS
        assert projected["count"] == 2

    @pytest.mark.asyncio
    async def test_lifted_images_do_not_displace_the_env(self, monkeypatch) -> None:
        # The images lift must stay on the response top level. Seeding a `result`
        # wrapper instead would make `{"images": [...]}` the whole env.
        image = {"url": "data:image/png;base64,AAA", "mime": "image/png"}
        envelope = await self._envelope(
            monkeypatch,
            {
                "answer_raw": "raw",
                "answer": "Detected 2 object(s).",
                "context": {},
                "tool_results": {"vision_bbox_detect": {"images": [image]}},
                "addons": {},
                "data": {"detections": _DETECTIONS, "count": 2},
            },
        )

        projected = project_outputs(envelope["result"], _DETECT_OUTPUTS)

        assert projected["answer"] == "Detected 2 object(s)."
        assert projected["detections"] == _DETECTIONS
        assert projected["count"] == 2
        assert envelope["result"]["images"] == [image]
