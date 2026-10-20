from __future__ import annotations

from collections.abc import Callable
from typing import Any

from src.domains.agentverse.agents.roles.role_spec import RoleSpec
from src.domains.agentverse.registries.base import Registry
from src.domains.agentverse.registries.utils.register_registry import register_registry


@register_registry(name="Role Registry", icon="🎭")
class RoleRegistry(Registry[RoleSpec]):
    """Registry for EVA roles (policies + phase handlers).

    Stores the ``RoleSpec`` itself. Roles carry live handler callables that
    ``to_metadata()`` strips, so the spec - not a class - is the entry; the
    stripped manifest is the item metadata the frontend reads.
    """

    name = "role"
    description = "Registry for EVA roles (policies + handlers)"

    def __init__(self) -> None:
        super().__init__(name=self.name)

    def _validate_component(self, component: RoleSpec) -> None:
        if not hasattr(component, "handlers") or not hasattr(component, "policy"):
            raise TypeError("RoleRegistry expects a RoleSpec instance")

    def register_role_spec(self, spec: RoleSpec) -> RoleSpec:
        """Register a ``RoleSpec`` under its own canonical key."""
        manifest = spec.to_metadata()
        description = f"{manifest.get('title') or spec.name} - {manifest.get('summary') or 'Role'}"

        self.register(
            name=spec.name,
            description=description[:300],  # keep it short for indexes
            version=spec.version,
            metadata=manifest,  # <-- FE gets everything here
            key=spec.key,
            component=spec,
        )
        return spec

    def bind_spec(self, spec: RoleSpec) -> Callable[[type], type]:
        """
        Bind a RoleSpec to a role class. Re-binding refreshes the registered spec,
        so a module re-executed by the loader brings its new handlers with it.
        Usage:
            @role_registry.bind_spec(RoleSpec(...))
            class GeneralRole: ...
        """

        def _decorator(cls: type) -> type:
            self.register_role_spec(spec)
            return cls

        return _decorator

    def get_role(self, key: str) -> RoleSpec:
        try:
            return self.get(key)
        except KeyError:
            raise LookupError(f"Role not found: {key}")

    def list_public(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for item in self.items():
            spec = self.get(item.key)
            out.append(
                {
                    "key": item.key,
                    "name": item.name,
                    "version": item.version,
                    "description": item.description or "",
                    "policy": spec.policy.model_dump(),
                    "agent_group": spec.agent_group,
                    "agent_subclass": spec.agent_subclass,
                    "handlers": list(spec.handlers.keys()),
                }
            )
        return out
