from __future__ import annotations

from typing import Any


def normalize_execute_inputs(manager: Any, key: str, kwargs: dict) -> tuple[str, dict]:
    return key, kwargs


def normalize_key(name: str) -> str:
    return name.lower().replace(" tool", "").strip().replace(" ", "_")
