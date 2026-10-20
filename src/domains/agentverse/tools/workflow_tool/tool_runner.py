from __future__ import annotations

from collections.abc import Callable
from typing import Any

from src.domains.agentverse.tools.base import ToolExecutionError
from src.domains.agentverse.tools.workflow_tool.protocols import ToolEnv


async def stable_tool_runner(
    *,
    tool_runner: Callable[..., Any],
    tool_key: str,
    kwargs: dict[str, Any] | None,
    context: dict[str, Any],
) -> tuple[bool, ToolEnv]:
    payload = dict(kwargs or {})

    existing_context = payload.get("context")
    if isinstance(existing_context, dict):
        payload["context"] = {**context, **existing_context}
    else:
        payload["context"] = context

    returned = await tool_runner(tool_key, payload)

    if not (isinstance(returned, tuple) and len(returned) == 2):
        # Accepting a bare payload here would hand the engine a wrapper instead
        # of a flat env, which silently resolves every declared output path to
        # nothing. Fail where the contract is broken, not where it is read.
        raise ToolExecutionError(
            f"tool_runner for '{tool_key}' must return (ok, env), got {type(returned).__name__}"
        )

    ok, env = returned
    return bool(ok), env


def wrap_tool_runner(*, tool_runner: Callable[..., Any], context: dict[str, Any]):
    async def _wrapped_runner(
        child_tool_key: str,
        kwargs: dict[str, Any] | None = None,
    ) -> tuple[bool, ToolEnv]:
        return await stable_tool_runner(
            tool_runner=tool_runner,
            tool_key=child_tool_key,
            kwargs=kwargs,
            context=context,
        )

    return _wrapped_runner


def require_tool_runner(context: dict[str, Any]) -> Callable[..., Any]:
    tool_runner = context.get("tool_runner")

    if not callable(tool_runner):
        raise ToolExecutionError("WorkflowTool requires context['tool_runner'] async callable.")

    return tool_runner
