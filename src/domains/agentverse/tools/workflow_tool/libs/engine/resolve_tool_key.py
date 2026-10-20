from __future__ import annotations

import re
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from src.domains.agentverse.utils.canonicalize import canonicalize

if TYPE_CHECKING:
    from src.domains.agentverse.tools.workflow_tool.models import WorkflowDefinition

_LEADING_TOOL_KEY_RE = re.compile(r"^@([A-Za-z0-9][\w-]*)(?![\w-])")


def resolve_tool_key_from_command(command: str) -> str | None:
    match = _LEADING_TOOL_KEY_RE.match(command.strip())
    if not match:
        return None
    return canonicalize(match.group(1))


def iter_step_tool_refs(definition: WorkflowDefinition) -> Iterator[tuple[Any, str | None]]:
    """Yield ``(step, tool_key)`` for every ``type == "tool"`` step carrying a command.

    ``tool_key`` is ``None`` when the command has no leading ``@key`` anchor. Save-time
    validation reports that step as an authoring error; callers that only want the tool
    set drop it. Both read the DAG through here so they can never disagree about which
    tools a workflow reaches for.
    """
    for step in definition.steps or []:
        if getattr(step, "type", "tool") != "tool":
            continue
        command = getattr(step, "command", None)
        if not command:
            continue
        yield step, resolve_tool_key_from_command(command)


def tool_keys_in_definition(definition: WorkflowDefinition) -> set[str]:
    """Canonical tool keys the definition's steps invoke."""
    return {key for _step, key in iter_step_tool_refs(definition) if key}
