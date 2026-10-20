from __future__ import annotations

from typing import Any

from src.domains.agentverse.tools.workflow_tool.helpers.history import (
    inject_safe_history as _inject_safe_history,
)


def _ensure_context(context: dict[str, Any] | None) -> dict[str, Any]:
    return context if isinstance(context, dict) else {}


def prepare_context(context: dict[str, Any] | None) -> dict[str, Any]:
    context = _ensure_context(context)
    _inject_safe_history(context)
    return context


def normalize_op(op: str | None) -> str:
    return (op or "start").strip().lower()
