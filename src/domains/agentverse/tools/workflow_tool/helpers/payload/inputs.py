from __future__ import annotations

import json
from typing import Any
from urllib.parse import unquote

from src.domains.agentverse.tools.base import ToolExecutionError


def normalize_inputs_payload(value: Any) -> dict[str, Any]:
    """Normalise a raw ``--inputs=`` value to a plain dict.

    Accepts:
    - ``dict`` — returned as-is.
    - ``str`` — URL-decoded then JSON-parsed. The decoded value must be a JSON
      object, otherwise a :class:`ToolExecutionError` is raised.
    - ``None`` / empty string — returns ``{}``.
    """
    if value is None:
        return {}

    if isinstance(value, dict):
        return value

    if isinstance(value, str):
        raw = value.strip()

        if not raw:
            return {}

        decoded = unquote(raw)

        try:
            parsed = json.loads(decoded)
        except json.JSONDecodeError as exc:
            raise ToolExecutionError(f"Invalid inputs JSON: {decoded}") from exc

        if not isinstance(parsed, dict):
            raise ToolExecutionError("inputs must decode to an object")

        return parsed

    raise ToolExecutionError(f"inputs must be dict or JSON string, got {type(value).__name__}")
