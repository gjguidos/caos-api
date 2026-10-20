from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from src.domains.agentverse.tools.workflow_tool.models import WorkflowRun

WorkflowStepEventCallback = Callable[[dict[str, Any]], Awaitable[None]]

# The `result` object a tool returned, flat - never a wrapper around one. The
# envelope layer (`tool_manager_v1.envelope.as_envelope`) has already peeled any
# nested `result`/`data` layer, so this is what declared output `path`s resolve
# against. Keep it that way: a wrapper here silently breaks every
# `${outputs.<step>.<key>}` binding.
#
# Remote tools produce a known shape: `base.integrations.mcp.proxy_contract.ToolEnv`.
# Local tools still return arbitrary dicts, so this stays untyped for now.
ToolEnv = dict[str, Any]


class ToolRunnerLike(Protocol):
    async def __call__(
        self,
        tool_key: str,
        kwargs: dict[str, Any],
    ) -> tuple[bool, ToolEnv]: ...


class WorkflowEngineLike(Protocol):
    async def resume_step(
        self,
        *,
        run: WorkflowRun,
        step_id: str,
        payload: dict[str, Any] | None = None,
        store: Any,
        on_step_event: WorkflowStepEventCallback | None = None,
    ) -> WorkflowRun: ...

    async def cancel_pause(
        self,
        *,
        run: WorkflowRun,
        step_id: str,
        payload: dict[str, Any] | None = None,
        store: Any,
        on_step_event: WorkflowStepEventCallback | None = None,
    ) -> WorkflowRun: ...
