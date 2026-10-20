import json
import re
from typing import Any
from urllib.parse import unquote

from src.domains.agentverse.tools.base import ToolExecutionError


def normalize_pause_payload(value: Any) -> dict[str, Any]:
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
            raise ToolExecutionError(f"Invalid pause_payload JSON: {decoded}") from exc

        if not isinstance(parsed, dict):
            raise ToolExecutionError("pause_payload must decode to an object")

        return parsed

    raise ToolExecutionError(
        f"pause_payload must be dict or JSON string, got {type(value).__name__}"
    )


def extract_flag_from_text(text: str, flag: str) -> str | None:
    if not isinstance(text, str):
        return None

    match = re.search(rf"--{re.escape(flag)}=([^\s]+)", text)

    if not match:
        return None

    return unquote(match.group(1))


def nested_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    nested = kwargs.get("kwargs")
    return nested if isinstance(nested, dict) else {}


def first_non_empty(*values: Any) -> Any:
    for value in values:
        if value not in (None, {}, ""):
            return value
    return None
