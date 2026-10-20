from __future__ import annotations

from typing import Any

from src.domains.agentverse.tools.workflow_tool.config import WorkflowToolConfig
from src.domains.agentverse.tools.workflow_tool.store import (
    InMemoryRunStore,
    RedisWorkflowRunStore,
    RunStore,
)


def workflow_store_prefix(context: dict[str, Any] | None) -> str:
    if not isinstance(context, dict):
        return "wf"

    session_id = context.get("_session_id") or context.get("session_id")
    if not session_id:
        return "wf"

    safe_session_id = str(session_id).replace(":", "_").strip()
    return f"wf:session:{safe_session_id}" if safe_session_id else "wf"


def resolve_run_store(
    *,
    tool: Any,
    context: dict[str, Any] | None,
    cfg: WorkflowToolConfig,
    memory_run_store: InMemoryRunStore,
) -> RunStore:
    if isinstance(context, dict):
        injected = context.get("workflow_run_store")
        if injected is not None:
            return injected

    repo = getattr(tool, "cache", None)
    if repo is not None:
        return RedisWorkflowRunStore(
            repo=repo,
            prefix=workflow_store_prefix(context),
            ttl_seconds=cfg.run_ttl_seconds,
        )

    return memory_run_store
