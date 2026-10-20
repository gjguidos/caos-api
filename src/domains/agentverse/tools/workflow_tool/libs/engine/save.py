from __future__ import annotations

from typing import Any

from src.domains.agentverse.tools.workflow_tool.models import (
    WorkflowRun,
    now_utc_iso,
)


async def save(run: WorkflowRun, store: Any) -> None:
    run.updated_at = now_utc_iso()
    await store.put(run)
