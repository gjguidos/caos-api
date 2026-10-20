from __future__ import annotations

from typing import Any

_REGISTERED_REGISTRIES: dict[str, dict[str, Any]] = {}


def register_registry(
    name: str,
    icon: str = "📘",
):
    def decorator(cls):
        registry_key = getattr(
            cls,
            "name",
            cls.__name__,
        )

        _REGISTERED_REGISTRIES[registry_key] = {
            "key": registry_key,
            "name": name,
            "icon": icon,
            "class": cls,
            "class_name": cls.__name__,
            "version": getattr(
                cls,
                "version",
                None,
            ),
            "description": getattr(
                cls,
                "description",
                None,
            ),
        }

        return cls

    return decorator