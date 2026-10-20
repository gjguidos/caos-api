from __future__ import annotations

from typing import Any


def inject_safe_history(context: dict[str, Any]) -> None:
    hist = context.get("history")
    safe_hist: list[dict[str, Any]] = []
    if isinstance(hist, list):
        for m in hist:
            if isinstance(m, dict):
                role = m.get("role")
                content = m.get("content")
                if isinstance(content, str):
                    safe_hist.append({"role": role, "content": content})
    context["workflow_history"] = safe_hist
