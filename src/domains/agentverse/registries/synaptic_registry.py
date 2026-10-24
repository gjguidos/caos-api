from __future__ import annotations

import inspect
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeAlias, overload

from src.domains.agentverse.registries.base import Registry, RegistryItem
from src.domains.agentverse.registries.utils.register_registry import register_registry

SynapticHandler: TypeAlias = Callable[..., Awaitable[Any]]

_SIGNAL_PATTERN = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+")


@dataclass(frozen=True, slots=True)
class SynapticBinding:
    signal: str
    provider: str
    handler: SynapticHandler
    version: str = "1.0.0"


@register_registry(name="Synaptic Registry", icon="🧠")
class SynapticRegistry(Registry[SynapticBinding]):
    """NCSC PBX capability declarations, not event contracts or dispatch.

    Trusted bootstrap imports decorators, then may call ``freeze()`` before
    exposing a runtime. Development module reloads may refresh the same provider
    and function origin only while unfrozen. A frozen registry is not reopened;
    restart/rebuild it to change declarations. This is not an authorization API.
    """

    name = "synaptic"
    description = "NCSC PBX capability registry"
    version = "0.1.0"

    def __init__(self) -> None:
        super().__init__(name=self.name)
        self._frozen = False

    @property
    def frozen(self) -> bool:
        return self._frozen

    def freeze(self) -> None:
        """End trusted registration; does not grant execution permissions."""
        self._frozen = True

    def _require_mutable(self) -> None:
        if self._frozen:
            raise RuntimeError("SynapticRegistry is frozen")

    @staticmethod
    def _validate_signal(signal: str) -> None:
        if not isinstance(signal, str) or not _SIGNAL_PATTERN.fullmatch(signal):
            raise ValueError(f"Invalid synaptic signal: {signal!r}")

    @staticmethod
    def _handler_origin(handler: SynapticHandler) -> tuple[str, str]:
        # The legacy loader also imports application modules without `src.`.
        return handler.__module__.removeprefix("src."), handler.__qualname__

    def _validate_component(self, component: SynapticBinding) -> None:
        if not isinstance(component, SynapticBinding):
            raise TypeError("SynapticRegistry requires SynapticBinding")
        self._validate_signal(component.signal)
        for label, value in (("provider", component.provider), ("version", component.version)):
            if not isinstance(value, str) or not value or value != value.strip():
                raise ValueError(f"Synaptic {label} must be a non-empty, unpadded string")
        if not inspect.iscoroutinefunction(component.handler):
            raise TypeError(f"Synaptic handler must be async: {component.signal}")
        if not all(
            isinstance(getattr(component.handler, attr, None), str)
            for attr in ("__module__", "__qualname__")
        ):
            raise TypeError("Synaptic handler requires a stable module and qualified name")

    def subscribe(
        self, signal: str, *, provider: str, version: str = "1.0.0"
    ) -> Callable[[SynapticHandler], SynapticHandler]:
        """Declare an async handler without executing or constructing anything."""
        self._validate_signal(signal)
        self._require_mutable()

        def decorator(handler: SynapticHandler) -> SynapticHandler:
            self.register(
                signal,
                component=SynapticBinding(signal, provider, handler, version),
                version=version,
            )
            return handler

        return decorator

    @overload
    def register(
        self,
        name: str,
        description: str | None = ...,
        version: str = ...,
        metadata: dict[str, Any] | None = ...,
        *,
        component: SynapticBinding,
        key: str | None = ...,
    ) -> SynapticBinding: ...

    @overload
    def register(
        self,
        name: str,
        description: str | None = ...,
        version: str = ...,
        metadata: dict[str, Any] | None = ...,
        component: None = ...,
        key: str | None = ...,
    ) -> Callable[[SynapticBinding], SynapticBinding]: ...

    def register(
        self,
        name: str,
        description: str | None = None,
        version: str = "1.0.0",
        metadata: dict[str, Any] | None = None,
        component: SynapticBinding | None = None,
        key: str | None = None,
    ) -> Callable[[SynapticBinding], SynapticBinding] | SynapticBinding:
        """AOSA direct/decorator registration with exact keys and guarded refreshes."""
        self._require_mutable()

        def decorator(binding: SynapticBinding) -> SynapticBinding:
            self._require_mutable()
            # Always validate: disabling generic component checks must not bypass this policy.
            self._validate_component(binding)
            signal = binding.signal
            if (key if key is not None else name) != signal or version != binding.version:
                raise ValueError("Registration key/version must match the synaptic binding")
            meta = dict(metadata or {})
            identity = {"signal": signal, "provider": binding.provider}
            if any(k in meta and meta[k] != value for k, value in identity.items()):
                raise ValueError("Registration metadata conflicts with the synaptic binding")
            meta.update(identity)
            existing = self._registry.get(signal)
            if existing is not None and (
                existing.provider != binding.provider
                or self._handler_origin(existing.handler) != self._handler_origin(binding.handler)
            ):
                raise ValueError(f"Conflicting synaptic registration for {signal!r}")

            # Like WorkflowRegistry, specialize registration but reuse AOSA's
            # single component/metadata stores, cache invalidation and catalog.
            item = self._store_item(
                signal,
                key=signal,
                name=name,
                description=description,
                version=version,
                metadata=meta,
            )
            # _store_item canonicalizes component keys; signals are exact addresses.
            item.key = signal
            self._store_value(signal, binding)
            if existing is None:
                self.registration_count += 1
            return binding

        return decorator(component) if component is not None else decorator

    def get(self, key: str, version: str | None = None) -> SynapticBinding:
        self._validate_signal(key)
        return self._resolve_store_key(key, version)

    def resolve(self, signal: str, version: str | None = None) -> SynapticBinding:
        return self.get(signal, version)

    def get_info(self, key: str) -> RegistryItem | None:
        self._validate_signal(key)
        if key not in self._info_cache:
            self._info_cache[key] = self._items.get(key)
        return self._info_cache[key]

    def resolve_key(self, value: str | None, *, kind: str) -> str:
        if value is None or value not in self:
            raise ValueError(f"Unknown {kind} {value!r}")
        return value

    def __contains__(self, key: str) -> bool:
        return key in self._registry

    def unregister(self, key: str) -> None:
        self._require_mutable()
        self._validate_signal(key)
        if key not in self._registry:
            raise KeyError(key)
        del self._registry[key]
        self._items.pop(key, None)
        self._info_cache.pop(key, None)
        for cache_key in [k for k in self._resolve_cache if k[0] == key]:
            del self._resolve_cache[cache_key]
