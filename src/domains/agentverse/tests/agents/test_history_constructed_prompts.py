"""
Tests for persisting tool-constructed prompts in conversation history.

When a context_based tool (e.g. @writer) runs, the prompt the model actually
saw (`user_overrides`) is persisted as the model-facing `content`, while the
raw @tool command is kept in `meta` for auditing only and never sent to the
model. Pure-tool / plain-chat turns are stored unchanged.
"""

from __future__ import annotations

import pytest

from src.domains.agentverse.agents.chat_agent.chat_agent import ChatAgent
from src.domains.agentverse.agents.mixins.utils.LLMCallsMixin.chat_mixin import (
    _sanitize_messages,
)
from src.domains.agentverse.llm.helpers.datatypes import ContextDelta
from src.domains.agentverse.tools.tool_manager_v1.execution import invoker_path as invoker_path_mod


class _CapturingAgent:
    """Minimal stand-in that records append_entry calls."""

    name = "stub-agent"

    def __init__(self) -> None:
        self.entries: list[dict] = []

    async def append_entry(
        self,
        role: str,
        content: str,
        *,
        session_id: str | None = None,
        tool: str | None = None,
        name: str | None = None,
        meta: dict | None = None,
    ) -> None:
        self.entries.append(
            {"role": role, "content": content, "session_id": session_id, "meta": meta}
        )


# Bind the real method so we exercise production logic without BaseAgent wiring.
_store_user_turn = ChatAgent._store_user_turn

RAW = '@writer --mode=write --text="a product description" --style=casual'
RENDERED = "TASK\n- mode: write\n\nUser input\na product description"


class TestStoreUserTurn:
    @pytest.mark.asyncio
    async def test_context_tool_turn_stores_rendered_with_raw_in_meta(self) -> None:
        agent = _CapturingAgent()

        await _store_user_turn(
            agent,
            user_input=RAW,
            session_id="s1",
            rendered_user=RENDERED,
        )

        assert len(agent.entries) == 1
        entry = agent.entries[0]
        # Model-facing content is the expanded prompt, not the raw command.
        assert entry["content"] == RENDERED
        assert entry["role"] == "user"
        # Raw command preserved for audit only.
        assert entry["meta"] == {"raw_input": RAW}

    @pytest.mark.asyncio
    async def test_plain_turn_stores_raw_with_no_meta(self) -> None:
        agent = _CapturingAgent()

        await _store_user_turn(
            agent,
            user_input="hello there",
            session_id="s1",
            rendered_user=None,
        )

        entry = agent.entries[0]
        assert entry["content"] == "hello there"
        assert entry["meta"] is None


class TestRenderedUserText:
    """ContextDelta normalizes both string and multimodal (proxy) overrides to
    the text the model saw, so the chat agent persists a faithful, text-only
    history entry regardless of the tool kind."""

    def test_string_override_passes_through(self) -> None:
        assert ContextDelta(user_overrides=RENDERED).rendered_user_text == RENDERED

    def test_multimodal_override_extracts_text(self) -> None:
        # Proxy/external tools return text + image content parts (proxy_contract).
        overrides = [
            {"type": "input_text", "text": "first line"},
            {"type": "input_image", "image_url": "https://blob/sas..."},
            {"type": "input_text", "text": "second line"},
        ]
        delta = ContextDelta(user_overrides=overrides)
        # Images are dropped (per-turn attachments); text parts are joined.
        assert delta.rendered_user_text == "first line\nsecond line"

    def test_image_only_override_yields_none(self) -> None:
        delta = ContextDelta(
            user_overrides=[{"type": "input_image", "image_url": "https://blob/sas..."}]
        )
        assert delta.rendered_user_text is None

    def test_no_override_yields_none(self) -> None:
        assert ContextDelta().rendered_user_text is None
        assert ContextDelta(user_overrides="").rendered_user_text is None


class TestInvokerEnvelopeCarriesRenderedPrompt:
    """The invoker envelope must carry rendered_user even though as_envelope
    normalizes to a fixed key set that drops the llm `context`."""

    @pytest.mark.asyncio
    async def test_rendered_user_survives_envelope(self, monkeypatch) -> None:
        async def _fake_llm(**_kwargs):
            return {
                "answer_raw": {},
                "answer": "ok",
                "context": {
                    "attachments": [],
                    "evidence": [],
                    "hints": {},
                    "rendered_user": RENDERED,
                },
                "tool_results": {},
                "addons": {},
            }

        monkeypatch.setattr(invoker_path_mod, "llm", _fake_llm)

        class _Mgr:
            agent = object()

        envelope = await invoker_path_mod.execute_invoker_path(
            _Mgr(),
            key="writer",
            meta={},
            text=RAW,
            history=[],
            session_id="s1",
            policy=None,
            effective_ctx={},
        )

        assert envelope["rendered_user"] == RENDERED

    @pytest.mark.asyncio
    async def test_no_rendered_user_leaves_envelope_clean(self, monkeypatch) -> None:
        async def _fake_llm(**_kwargs):
            return {
                "answer_raw": {},
                "answer": "ok",
                "context": {"attachments": [], "evidence": [], "hints": {}},
                "tool_results": {},
                "addons": {},
            }

        monkeypatch.setattr(invoker_path_mod, "llm", _fake_llm)

        class _Mgr:
            agent = object()

        envelope = await invoker_path_mod.execute_invoker_path(
            _Mgr(),
            key="writer",
            meta={},
            text=RAW,
            history=[],
            session_id="s1",
            policy=None,
            effective_ctx={},
        )

        assert "rendered_user" not in envelope


class TestSanitizeMessages:
    def test_meta_is_stripped_before_reaching_model(self) -> None:
        # Simulates turn 2: the stored context_tool entry is replayed as history.
        history_entry = {
            "role": "user",
            "content": RENDERED,
            "meta": {"raw_input": RAW, "tool": "writer"},
        }

        cleaned = _sanitize_messages([history_entry])

        assert cleaned == [{"role": "user", "content": RENDERED}]
        # The model sees the expanded prompt and never the raw command / meta.
        assert "meta" not in cleaned[0]
        assert cleaned[0]["content"] == RENDERED

    def test_name_is_preserved_when_present(self) -> None:
        cleaned = _sanitize_messages(
            [{"role": "tool", "content": "x", "name": "writer", "meta": {"a": 1}}]
        )
        assert cleaned == [{"role": "tool", "content": "x", "name": "writer"}]

    def test_plain_messages_pass_through(self) -> None:
        msgs = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ]
        assert _sanitize_messages(msgs) == msgs
