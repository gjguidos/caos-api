from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any, ClassVar, Generic, TypeVar, overload

from pydantic import BaseModel, ConfigDict, Field

from src.domains.agentverse.entities.resource_exposure import ResourceExposure, classify_resource
from src.domains.agentverse.exceptions import RegistrationError
from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.utils.canonicalize import canonicalize

T = TypeVar("T")

def _entry_label(entry: Any) -> str:
    """Short human label for an entry, for conflict messages and logs."""
    return getattr(entry, "__name__", None) or type(entry).__name__


class RegistryItem(BaseModel):
    """Metadata structure for a registered component."""

    key: str
    name: str
    description: str | None = None
    version: str = "1.0.0"
    metadata: dict[str, Any] = Field(default_factory=dict)
    registration_time: datetime = Field(default_factory=datetime.now)

    model_config = ConfigDict(extra="allow")

    @property
    def exposure(self) -> ResourceExposure:
        """Normalized catalog classification; does not change serialized metadata."""
        return classify_resource(self.metadata).exposure


class Registry(Generic[T]):
    """
    NERV-HQ themed registry system for the AgentVerse.

    Manages registration, instantiation, and metrics for EVA-compatible components.
    """
    _instances: ClassVar[dict[str, Registry[Any]]] = {}
    _catalog_logged: ClassVar[bool] = False

    name: ClassVar[str] = "base_registry"
    description: ClassVar[str] = "Base component registry"
    version: ClassVar[str] = "1.1.0"

    def __init__(
        self,
        name: str,
        validate_components: bool = True,
        track_metrics: bool = True,
    ):
        self._name = name

        self._registry: dict[str, T] = {}
        self._items: dict[str, RegistryItem] = {}

        self._resolve_cache: dict[tuple[str, str | None], T] = {}
        self._info_cache: dict[str, RegistryItem | None] = {}

        self.validate_components = validate_components
        self.track_metrics = track_metrics
        self.registration_count = 0
        Registry._instances[self._name] = self

    def keys(self) -> list[str]:
        return list(self._registry.keys())
    
    def values(self) -> list[T]:
        return list(self._registry.values())


    @classmethod
    def catalog(cls) -> dict[str, dict[str, Any]]:
        return {
            key: registry.summary()
            for key, registry in cls._instances.items()
        }

    @classmethod
    def log_catalog(cls) -> None:
        catalog = cls.catalog()

        AGENTVERSE_LOGGER.info(
            LogDomain.INTERNAL_INTELLIGENCE,
            (
                "[📚 REGISTRY][✅ CATALOG][🪐AGENTVERSE] "
                f"registered={len(catalog)} "
                f"registries={list(catalog.keys())}"
            ),
            extra={
                "registry_count": len(catalog),
                "registries": catalog,
            },
        )

    def _store_item(
        self,
        store_key: str,
        *,
        key: str,
        name: str,
        description: str | None = None,
        version: str = "1.0.0",
        metadata: dict[str, Any] | None = None,
    ) -> RegistryItem:
        """Persist a ``RegistryItem`` under ``store_key`` (the canonical key)."""
        item = RegistryItem(
            key=canonicalize(key),
            name=name,
            description=description,
            version=version,
            metadata=metadata or {},
        )
        self._items[store_key] = item
        self._info_cache.pop(store_key, None)

        reg_name = self._name.replace("_", " ").upper()

        AGENTVERSE_LOGGER.info(
            LogDomain.NERV_HQ,
            f"[🧭 REGISTRY][✅ {reg_name}] item name={name}",
            extra={
                "registry_name": self._name,
                "workflow_key": key,
                "version": version,
                "registration_count": self.registration_count,
            },
        )
        return item

    def _store_value(
        self,
        store_key: str,
        value: T,
    ) -> None:
        self._registry[store_key] = value

        for cache_key in [
            k
            for k in self._resolve_cache
            if k[0] == store_key
        ]:
            self._resolve_cache.pop(cache_key, None)

    @overload
    def register(
        self,
        name: str,
        description: str | None = ...,
        version: str = ...,
        metadata: dict[str, Any] | None = ...,
        *,
        component: T,
        key: str | None = ...,
    ) -> T: ...

    @overload
    def register(
        self,
        name: str,
        description: str | None = ...,
        version: str = ...,
        metadata: dict[str, Any] | None = ...,
        component: None = ...,
        key: str | None = ...,
    ) -> Callable[[T], T]: ...

    def register(
        self,
        name: str,
        description: str | None = None,
        version: str = "1.0.0",
        metadata: dict[str, Any] | None = None,
        component: T | None = None,
        key: str | None = None,
    ) -> Callable[[T], T] | T:
        """Register a component under its canonical key.

        Registration is last-write-wins: a key already in use is overwritten, entry
        and item together. Re-registration is the normal case, not an error - the
        auto-router loader re-executes modules (and ``pythonpath = [".", "src"]``
        makes one source module importable under two dotted paths), so the same
        component arrives repeatedly and must simply refresh, bringing its new
        handler callables with it.
        """
        store_key = canonicalize(key) if key else canonicalize(name)
        if not store_key:
            raise RegistrationError(message=f"Registry '{self._name}' requires a non-empty key")

        def store(comp: T) -> RegistryItem:
            return self._store_item(
                store_key,
                key=store_key,
                name=name,
                description=description or getattr(comp, "__doc__", None),
                version=version,
                metadata=metadata or {},
            )

        def decorator(comp: T) -> T:
            try:
                existing = self._registry.get(store_key)

                if self.validate_components:
                    self._validate_component(comp)

                # Overwriting is a complete replacement: `_store_value` drops the stale
                # resolve-cache entries and `_store_item` the stale info-cache entry.
                self._store_value(store_key, comp)
                store(comp)
                if existing is None:
                    self.registration_count += 1

                AGENTVERSE_LOGGER.debug(
                    LogDomain.NERV_HQ,
                    f"[📁 REGISTRY][🧠 COMMIT] register_complete '{name}'",
                    extra={
                        "registry_name": self._name,
                        "component_name": name,
                        "key": store_key,
                        "component_type": _entry_label(comp),
                        "version": version,
                        "overwrote_existing": existing is not None,
                        "registration_count": self.registration_count,
                    },
                )
                return comp

            except Exception as e:
                AGENTVERSE_LOGGER.warning(
                    LogDomain.NERV_HQ,
                    "[📁 REGISTRY][💥 FAIL] register_failed",
                    extra={
                        "registry_name": self._name,
                        "component_name": name,
                        "version": version,
                        "component_type": _entry_label(comp),
                        "error": str(e),
                    },
                )
                if isinstance(e, RegistrationError):
                    raise
                raise RegistrationError(
                    message=f"Failed to register component '{name}': {str(e)}",
                    details={"name": name, "version": version, "type": _entry_label(comp)},
                )

        return decorator(component) if component is not None else decorator

    def get(self, key: str, version: str | None = None) -> T:
        """Resolve a registered entry by its canonical key.

        Keys are the single addressing contract for registries: display names
        and class names are not accepted. ``canonicalize(key)`` is the store key.
        Raises ``KeyError`` if the key is unknown - there is no name-based fallback.
        """
        return self._resolve_store_key(canonicalize(key), version)

    def resolve_key(self, value: str | None, *, kind: str) -> str:
        """Resolve a caller-supplied string onto the canonical key it addresses.

        Accepts any spelling that canonicalizes onto a registered key ("Chat" -> "chat",
        "Datetime Tool" -> "datetime_tool") and returns the key itself, so whatever is
        persisted always matches the key the registry later resolves by.

        Raises ``ValueError`` if it addresses nothing registered.
        """
        key = canonicalize(value)
        if not key or key not in self._registry:
            raise ValueError(f"Unknown {kind} '{value}'. It must match a registered {kind} key.")
        return key

    def _resolve_store_key(self, store_key: str, version: str | None = None) -> T:
        """Resolve the entry registered under ``store_key`` (the canonical key).

        Kept distinct from :meth:`get` because it skips re-canonicalization: it is
        the hot path used by :meth:`get` (which already canonicalized) and by
        registries with their own key-normalizing ``get`` overrides.
        """
        ck = (store_key, version)
        if ck in self._resolve_cache:
            AGENTVERSE_LOGGER.debug(
                LogDomain.NERV_HQ,
                "[📁 REGISTRY][⚡ CACHE] resolve_hit",
                extra={
                    "registry_name": self._name,
                    "component_name": store_key,
                    "version": version or "*",
                },
            )
            return self._resolve_cache[ck]

        if store_key not in self._registry:
            AGENTVERSE_LOGGER.warning(
                LogDomain.NERV_HQ,
                "[📁 REGISTRY][❌ LOOKUP] resolve_missing",
                extra={
                    "registry_name": self._name,
                    "component_name": store_key,
                    "version": version,
                },
            )
            raise KeyError(f"Component with key '{store_key}' not found in {self._name} registry")

        component = self._registry[store_key]
        item = self._items.get(store_key)

        if version and item and item.version != version:
            AGENTVERSE_LOGGER.warning(
                LogDomain.NERV_HQ,
                "[📁 REGISTRY][❌ VERSION] resolve_mismatch",
                extra={
                    "registry_name": self._name,
                    "component_name": store_key,
                    "requested_version": version,
                    "available_version": item.version,
                },
            )
            raise KeyError(
                f"Component '{store_key}' version {version} not found (found {item.version})"
            )

        self._resolve_cache[ck] = component

        AGENTVERSE_LOGGER.debug(
            LogDomain.NERV_HQ,
            "[📁 REGISTRY][🧠 RESOLVE] resolve_complete",
            extra={
                "registry_name": self._name,
                "component_name": store_key,
                "version": version or (item.version if item else None),
            },
        )
        return component

    def get_info(self, key: str) -> RegistryItem | None:
        """Resolve a component's ``RegistryItem`` by its canonical key.

        Canonicalizes like :meth:`get`, so both public lookups accept the same
        spellings and the info cache is only ever keyed by the canonical key.
        """
        store_key = canonicalize(key)
        if store_key in self._info_cache:
            AGENTVERSE_LOGGER.debug(
                LogDomain.NERV_HQ,
                "[📁 REGISTRY][⚡ CACHE] info_hit",
                extra={
                    "registry_name": self._name,
                    "component_name": store_key,
                },
            )
            return self._info_cache[store_key]

        item = self._items.get(store_key)
        self._info_cache[store_key] = item

        AGENTVERSE_LOGGER.debug(
            LogDomain.NERV_HQ,
            "[📁 REGISTRY][🔖 INFO] info_complete",
            extra={
                "registry_name": self._name,
                "component_name": store_key,
                "found": item is not None,
            },
        )
        return item

    def items(self) -> list[RegistryItem]:
        """Every registered ``RegistryItem``, in registration order.

        The public way to scan a registry's metadata, so callers never need to
        reach into ``_items``.
        """
        return list(self._items.values())

    def unregister(self, key: str) -> None:
        """Remove the component registered under ``key`` (canonicalized) and all
        of its cached state. Raises ``KeyError`` if the key is not registered.
        """
        store_key = canonicalize(key)
        if store_key not in self._registry:
            raise KeyError(key)
        self._registry.pop(store_key, None)
        self._items.pop(store_key, None)
        self._info_cache.pop(store_key, None)
        for cache_key in [k for k in self._resolve_cache if k[0] == store_key]:
            self._resolve_cache.pop(cache_key, None)

    def list(self, include_metadata: bool = False) -> list[Any]:
        if include_metadata:
            component_list = []
            for store_key, item in self._items.items():
                entry = item.model_dump()
                if not entry.get("description"):
                    entry["description"] = (
                        getattr(self._registry.get(store_key), "__doc__", "") or ""
                    )
                component_list.append(entry)
            AGENTVERSE_LOGGER.debug(
                LogDomain.NERV_HQ,
                "[📁 REGISTRY][📝 LIST] list_metadata",
                extra={
                    "registry_name": self._name,
                    "count": len(component_list),
                },
            )
            return component_list

        component_names = list(self._registry.keys())
        AGENTVERSE_LOGGER.debug(
            LogDomain.NERV_HQ,
            "[📁 REGISTRY][📝 LIST] list_names",
            extra={
                "registry_name": self._name,
                "count": len(component_names),
            },
        )
        return component_names

    def get_metrics(self) -> dict[str, Any]:
        return self.summary()
    
    def summary(self) -> dict[str, Any]:
        return {
            "name": self._name,
            "version": self.version,
            "component_count": len(self),
            "registered_types": list(self._registry.keys()),
            "status": "active" if len(self) else "empty",
        }

    def log_summary(self) -> None:
        summary = self.summary()

        AGENTVERSE_LOGGER.info(
            LogDomain.NERV_HQ,
            (
                "[📚 REGISTRY][✅ SUMMARY] "
                f"name={self._name} "
                f"components={summary['component_count']} "
                f"registered={summary['registered_types']}"
            ),
            extra=summary,
        )

    def _validate_component(self, component: T) -> None:
        pass  # Extend in subclass

    def __contains__(self, key: str) -> bool:
        return canonicalize(key) in self._registry

    def __len__(self) -> int:
        return len(self._registry)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name='{self._name}', components={len(self)})"
