"""`stable_tool_runner` enforces the `(ok, env)` runner contract.

A runner that returns a bare payload used to be accepted and passed through as
the env. That env is what declared output `path`s resolve against, so a wrapper
arriving here resolves every `${outputs.<step>.<key>}` binding to nothing - a
silent failure far from its cause. It is rejected at the seam instead.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.domains.agentverse.tools.base import ToolExecutionError
from src.domains.agentverse.tools.workflow_tool.tool_runner import stable_tool_runner


async def _call(returned: Any) -> tuple[bool, Any]:
    async def runner(tool_key: str, kwargs: dict[str, Any]) -> Any:
        return returned

    return await stable_tool_runner(
        tool_runner=runner,
        tool_key="writer",
        kwargs={"text": "hi"},
        context={"session_id": "s1"},
    )


class TestContract:
    @pytest.mark.asyncio
    async def test_a_tuple_passes_through(self) -> None:
        assert await _call((True, {"answer": "hi"})) == (True, {"answer": "hi"})

    @pytest.mark.asyncio
    async def test_ok_is_coerced_to_bool(self) -> None:
        ok, _ = await _call((1, {"answer": "hi"}))

        assert ok is True

    @pytest.mark.parametrize(
        "returned",
        [
            {"success": True, "result": {"answer": "hi"}},
            {"answer": "hi"},
            "plain text",
            None,
        ],
    )
    @pytest.mark.asyncio
    async def test_a_non_tuple_return_is_rejected(self, returned: Any) -> None:
        with pytest.raises(ToolExecutionError, match="must return \\(ok, env\\)"):
            await _call(returned)


class TestContextMerge:
    @pytest.mark.asyncio
    async def test_the_step_context_wins_over_the_run_context(self) -> None:
        seen: dict[str, Any] = {}

        async def runner(tool_key: str, kwargs: dict[str, Any]) -> tuple[bool, Any]:
            seen.update(kwargs)
            return True, {}

        await stable_tool_runner(
            tool_runner=runner,
            tool_key="writer",
            kwargs={"context": {"a": "step"}},
            context={"a": "run", "b": "run"},
        )

        assert seen["context"] == {"a": "step", "b": "run"}
