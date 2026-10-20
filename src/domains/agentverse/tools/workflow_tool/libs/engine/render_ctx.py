from __future__ import annotations

from typing import Any

from src.domains.agentverse.tools.workflow_tool.helpers.output_store import read_binding_outputs
from src.domains.agentverse.tools.workflow_tool.helpers.response import dump_model
from src.domains.agentverse.tools.workflow_tool.models import (
    WorkflowRun,
)


def render_ctx(run: WorkflowRun) -> dict[str, Any]:
    return {
        "inputs": run.inputs or {},
        "state": run.state or {},
        "outputs": read_binding_outputs(run.state),
        "resume": (run.state or {}).get("resume_payloads", {}),
        "paused": {key: dump_model(value) for key, value in (run.paused or {}).items()},
        "waiting_events": {
            key: dump_model(value) for key, value in (run.waiting_events or {}).items()
        },
    }
