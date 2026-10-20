# state_patch.py
from typing import Any

from src.domains.agentverse.tools.workflow_tool.helpers.templating import deep_set


def apply_state_patch(run, patch: dict[str, Any]) -> None:
    for path, value in patch.items():
        deep_set(run.state, path, value)
