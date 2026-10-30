"""Resolve a trusted registered Tool configuration model, without constructing a Tool."""

import importlib
from typing import Any

from pydantic import BaseModel


def config_contract(schema_ref: Any) -> type[BaseModel] | None:
    if isinstance(schema_ref, type):
        return schema_ref if issubclass(schema_ref, BaseModel) else None
    if not isinstance(schema_ref, str) or "." not in schema_ref:
        return None
    module_path, _, name = schema_ref.rpartition(".")
    try:
        candidate = getattr(importlib.import_module(module_path), name, None)
    except ImportError:
        return None
    return candidate if isinstance(candidate, type) and issubclass(candidate, BaseModel) else None
