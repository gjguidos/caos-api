"""Tests for the ToolResult shape `MCPProxyTool.execute` hands back.

The `result` dict here becomes the step `env` the workflow engine resolves
declared output `path`s against, so every key the proxy contract produces has to
reach it - `execute` forwards the normalized payload rather than re-projecting
it key by key.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.base.integrations.mcp.models import MCPToolExecuteResponse
from src.domains.agentverse.tools.spec import ToolSpec


class _FakeClient:
    def __init__(self, result: Any, *, success: bool = True, error: str | None = None) -> None:
        self._result = result
        self._success = success
        self._error = error
        self.calls: list[dict[str, Any]] = []

    async def execute_tool(
        self, *, tool_name: str, method: str, arguments: dict[str, Any]
    ) -> MCPToolExecuteResponse:
        self.calls.append({"tool_name": tool_name, "method": method, "arguments": arguments})
        return MCPToolExecuteResponse(success=self._success, result=self._result, error=self._error)


def _tool(remote_result: Any, **client_kwargs: Any) -> Any:
    # Lazy: importing proxy_tool before the domains package is initialized hits
    # the registries -> proxy_tool cycle. The ToolSpec import above initializes
    # domains, so by here this is a cache hit.
    from src.base.integrations.mcp.proxy_tool import MCPProxyTool

    return MCPProxyTool(
        mcp_client=_FakeClient(remote_result, **client_kwargs),
        tool_spec=ToolSpec(key="image_sink", name="Image Sink Tool"),
    )


class TestExecuteResultShape:
    @pytest.mark.asyncio
    async def test_data_survives_the_projection(self) -> None:
        tool = _tool({"answer": "received slots: ['reference', 'source']", "data": {"slots": {}}})

        result = (await tool.execute(text="@image_sink")).result

        assert result["data"] == {"slots": {}}
        assert result["answer"] == "received slots: ['reference', 'source']"

    @pytest.mark.asyncio
    async def test_absent_data_leaves_no_key(self) -> None:
        tool = _tool({"answer": "emitted"})

        assert "data" not in (await tool.execute(text="@t")).result

    @pytest.mark.asyncio
    async def test_non_dict_data_is_dropped(self) -> None:
        tool = _tool({"answer": "emitted", "data": "not-an-object"})

        assert "data" not in (await tool.execute(text="@t")).result

    @pytest.mark.asyncio
    async def test_images_and_addons_still_pass_through(self) -> None:
        tool = _tool(
            {
                "answer": "emitted",
                "addons": {"note": "hi"},
                # Wire shape emitted by the remote `ImageOutput`; `_extract_images`
                # folds it into a data URI.
                "images": [{"data": "AA==", "mime": "image/png"}],
            }
        )

        result = (await tool.execute(text="@t")).result

        assert result["addons"] == {"note": "hi"}
        assert result["images"][0]["url"] == "data:image/png;base64,AA=="

    @pytest.mark.asyncio
    async def test_image_mime_and_alt_text_survive(self) -> None:
        tool = _tool(
            {
                "answer": "emitted",
                "images": [{"data": "AA==", "mime": "image/png", "alt_text": "a dot"}],
            }
        )

        image = (await tool.execute(text="@t")).result["images"][0]

        assert image["mime"] == "image/png"
        assert image["alt_text"] == "a dot"

    @pytest.mark.asyncio
    async def test_absent_alt_text_leaves_no_key(self) -> None:
        tool = _tool({"answer": "emitted", "images": [{"data": "AA==", "mime": "image/png"}]})

        assert "alt_text" not in (await tool.execute(text="@t")).result["images"][0]


class TestExecuteFailureSignal:
    """A remote failure must reach ToolResult.success, not be reported as OK.

    The workflow engine gates step success on the env's `success` flag, so a
    proxy tool that always reports success turns a failed MCP call into a
    COMPLETED step.
    """

    @pytest.mark.asyncio
    async def test_remote_failure_marks_the_result_failed(self) -> None:
        # `_call_remote` raises on a non-success response; `_run_env` catches it
        # and routes through `_error_env`, so the reason lands in `answer`.
        tool = _tool({"answer": "nope"}, success=False, error="remote blew up")

        result = await tool.execute(text="@t")

        assert result.success is False
        assert result.error == "remote blew up"
        assert result.result["answer"] == "remote blew up"
        assert result.result["addons"]["code"] == "mcp_run_failed"
        assert result.result["addons"]["tool_key"] == tool.key

    @pytest.mark.asyncio
    async def test_failure_reason_reaches_the_env(self) -> None:
        """The env - not just ToolResult - has to carry the reason.

        The workflow engine reads the step error off the env
        (`_extract_error`), so a reason that only reaches `ToolResult.error` is
        reported as a bare "Tool failed: <step_id>".
        """
        tool = _tool({"answer": "nope"}, success=False, error="remote blew up")

        env = (await tool.execute(text="@t")).result

        assert env["success"] is False
        assert env["error"] == "remote blew up"

    @pytest.mark.asyncio
    async def test_inner_failure_without_a_message_is_still_a_failure(self) -> None:
        """`success=False, error=None` is a shape the remote tool permits."""
        tool = _tool({"answer": "nope", "success": False})

        result = await tool.execute(text="@t")

        assert result.success is False
        assert result.error
        assert result.result["error"] == result.error

    @pytest.mark.asyncio
    async def test_success_is_unset_on_a_healthy_call(self) -> None:
        result = await _tool({"answer": "fine"}).execute(text="@t")

        assert result.success is True
        assert result.error is None
        assert "success" not in result.result
        assert "error" not in result.result
