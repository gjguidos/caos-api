from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import Request

from src.domains.agentverse.utils.canonicalize import canonicalize
from src.domains.agentverse.exceptions import RegistrationError
from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.registries.base import Registry
from src.domains.agentverse.registries.utils.register_registry import (
    register_registry,
)

RuntimeDependencyResolver = Callable[[Request], Any]


@register_registry(
    name="Tool Runtime Dependency",
    icon="🔌",
)
class ToolRuntimeDependencyRegistry(
    Registry[RuntimeDependencyResolver]
):
    name = "tool_runtime_dependency"
    description = (
        "Registry for resolving tool-specific runtime dependencies"
    )
    version = "1.0.0"

    def __init__(self) -> None:
        super().__init__(name=self.name)

    def _construct_key(self, key: str) -> str:
        return (key or "").strip().lower()

    def register(
        self,
        *,
        key: str,
        name: str | None = None,
        description: str | None = None,
        replace: bool = False,
    ):
        reg_key = canonicalize(key)

        def decorator(
            resolver: RuntimeDependencyResolver,
        ) -> RuntimeDependencyResolver:
            if not replace and reg_key in self._items:
                raise RegistrationError(
                    f"Runtime dependency '{reg_key}' "
                    "already registered"
                )

            self._store_value(
                reg_key,
                resolver,
            )

            self._store_item(
                reg_key,
                key=reg_key,
                name=name or reg_key,
                description=description,
                version=self.version,
                metadata={},
            )

            self.registration_count += 1

            return resolver

        return decorator

    def resolve(
        self,
        *,
        key: str,
        request: Request,
    ) -> Any:
        reg_key = self._construct_key(key)

        if reg_key not in self._registry:
            raise KeyError(
                f"Runtime dependency '{reg_key}' "
                f"not found in {self._name} registry"
            )

        resolver = self._registry[reg_key]

        return resolver(request)