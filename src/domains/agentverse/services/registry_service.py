from typing import Any

from src.domains.agentverse.registries.base import Registry, RegistryItem


class RegistryService:
    """
    A façade over our various NERV-HQ registries (agent, personality, tool).
    """

    def __init__(self, registries: dict[str, Registry[Any]]):
        # e.g. {"agent":agent_registry, "tool":tool_registry, ...}
        self._registries = registries

    def _get_registry(self, which: str) -> Registry[Any]:
        try:
            return self._registries[which]
        except KeyError:
            raise ValueError(f"Registry '{which}' not found")

    def list(
        self,
        which: str,
        include_metadata: bool = False,
        **kwargs,
    ) -> list[Any]:
        """
        List either the names or full metadata for a given registry.

        For 'tool' and include_metadata=True we default to the PUBLIC catalog
        (filters can be overridden via kwargs).
        """
        reg = self._get_registry(which)

        # If the registry provides a filtered public view and the caller wants metadata,
        # prefer that (default public-only behavior for tools).
        if include_metadata and hasattr(reg, "list_public") and which == "tool":
            # default public filter; allow overrides via kwargs
            defaults = dict(
                include_system=False,
                include_builtin=False,
                include_hidden=False,
                allowed_tags=None,
                blocked_tags=None,
            )
            defaults.update(kwargs or {})
            return reg.list_public(**defaults)

        # Fallback to the registry's normal list() (pass through kwargs if supported)
        try:
            return reg.list(include_metadata=include_metadata, **kwargs)  # type: ignore[arg-type]
        except TypeError:
            # registry.list doesn't accept extra kwargs
            return reg.list(include_metadata=include_metadata)

    def get_info(self, which: str, key: str) -> RegistryItem | None:
        """
        Get the RegistryItem for a single component, addressed by canonical key.
        """
        reg = self._get_registry(which)
        info = reg.get_info(key)
        if info is None:
            raise KeyError(f"Component with key '{key}' not found in registry '{which}'")
        return info

    def get_component(self, which: str, name: str, version: str | None = None) -> type[Any]:
        """
        Retrieve the actual class (agent, tool, personality, etc.).
        """
        reg = self._get_registry(which)
        return reg.get(name, version)

    def get_metrics(self, which: str) -> dict[str, Any]:
        """
        Pull performance/usage metrics from a registry.
        """
        reg = self._get_registry(which)
        return reg.get_metrics()
