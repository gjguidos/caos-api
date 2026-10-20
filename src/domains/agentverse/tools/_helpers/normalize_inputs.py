from __future__ import annotations

from collections.abc import Container
from typing import Any


def normalize_inputs(
    *,
    inputs: dict[str, Any] | None = None,
    kwargs: dict[str, Any] | None = None,
    reserved: Container[str] | None = None,
    ignore_blank_strings: bool = True,
    ignore_none: bool = True,
    strip_strings: bool = True,
) -> dict[str, Any]:
    """
    Merge explicit inputs with extra runtime kwargs.

    Rules:
    - explicit `inputs` wins over kwargs
    - nested `kwargs={...}` is flattened
    - reserved runtime keys are ignored
    - None values are ignored by default
    - blank strings are ignored by default
    - strings are stripped by default
    """
    reserved = reserved or set()

    normalized: dict[str, Any] = dict(inputs or {})
    raw_kwargs: dict[str, Any] = dict(kwargs or {})

    nested_kwargs = raw_kwargs.pop("kwargs", None)
    if isinstance(nested_kwargs, dict):
        raw_kwargs = {**nested_kwargs, **raw_kwargs}

    for key, value in raw_kwargs.items():
        if key in reserved:
            continue

        if value is None and ignore_none:
            continue

        if isinstance(value, str):
            if strip_strings:
                value = value.strip()

            if ignore_blank_strings and not value:
                continue

        normalized.setdefault(key, value)

    return normalized
