# depth_guard.py
from __future__ import annotations

import logging

from src.domains.agentverse.tools.tool_manager_v1.execution.envelope import error_envelope
from src.domains.agentverse.utils.run_trace import STATUS_BLOCKED

logger = logging.getLogger("agentverse.tools.tool_manager.depth_guard")


def apply_depth_guard(effective_ctx: dict, *, key: str) -> dict | None:
    """
    Mutates effective_ctx["_tool_depth"] like original.
    Returns an error envelope dict on failure, else None.
    """
    try:
        depth = int(effective_ctx.get("_tool_depth") or 0)
    except Exception:
        depth = 0

    if depth > 8:
        logger.error(
            "[TOOL MANAGER][EXECUTE] Tool nesting too deep (depth=%s) for key=%s",
            depth,
            key,
        )
        return error_envelope("Tool nesting too deep", kind=STATUS_BLOCKED)

    effective_ctx["_tool_depth"] = depth + 1
    return None
