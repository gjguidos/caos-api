from __future__ import annotations

from collections.abc import Iterable

from src.domains.agentverse.entities.workflow import slugify_workflow_key
from src.domains.agentverse.tools.base import ToolExecutionError


def authorize_workflow_key(
    key: str,
    *,
    declared: Iterable[str],
) -> None:
    """Raise unless ``key`` is one the agent is allowed to run.

    A caller-supplied key must not outrank the agent's own configuration, so the
    authorization set is exactly what the agent declares: its attached
    ``workflows``.

    Both sides are slugified before comparison: the catalog slugifies on read, so
    an attached key and a requested one can differ in case or spacing and still
    address the same row.
    """
    allowed = {slugify_workflow_key(k) for k in declared if k}

    if slugify_workflow_key(key) in allowed:
        return

    available = ", ".join(sorted(allowed)) or "none"
    raise ToolExecutionError(
        f"Workflow '{key}' is not attached to this agent and cannot be run. "
        f"Attached workflows: {available}."
    )
