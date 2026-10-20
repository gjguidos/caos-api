"""Single owner of the two step-output namespaces in ``run.state``.

A completed step is stored under two keys, and every write site must keep them
straight:

- ``outputs`` — the projected ``{key: value}`` binding namespace that
  ``${outputs.<step_id>.<key>}`` resolves against (see ``render_ctx``).
- ``display_outputs`` — the raw ``{"tool", "env"}`` wrapper the display layer
  reads (snapshot / chat bubble / canvas).

Routing every read and write through the constants and helpers here is what
keeps the split from silently diverging.
"""

from __future__ import annotations

from typing import Any

BINDING_OUTPUTS_KEY = "outputs"
DISPLAY_OUTPUTS_KEY = "display_outputs"


def display_wrapper(tool_key: str, env: Any) -> dict[str, Any]:
    """The raw display wrapper shape the display layer reads."""
    return {"tool": tool_key, "env": env}


def display_patch_key(step_id: str) -> str:
    return f"{DISPLAY_OUTPUTS_KEY}.{step_id}"


def binding_patch_key(step_id: str) -> str:
    return f"{BINDING_OUTPUTS_KEY}.{step_id}"


def store_display_output(state: dict[str, Any], step_id: str, tool_key: str, env: Any) -> None:
    """Write the display wrapper for a step, mutating ``state`` in place."""
    state.setdefault(DISPLAY_OUTPUTS_KEY, {})[step_id] = display_wrapper(tool_key, env)


def read_display_outputs(state: dict[str, Any] | None) -> dict[str, Any]:
    return (state or {}).get(DISPLAY_OUTPUTS_KEY) or {}


def read_binding_outputs(state: dict[str, Any] | None) -> dict[str, Any]:
    return (state or {}).get(BINDING_OUTPUTS_KEY) or {}
