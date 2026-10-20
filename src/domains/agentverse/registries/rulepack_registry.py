from collections.abc import Callable
from dataclasses import asdict, is_dataclass
from typing import Any

from src.domains.agentverse.agents.roles.rules.rulepack_spec import RulePackSpec, RuleSpec
from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.registries.base import Registry
from src.domains.agentverse.registries.utils.register_registry import register_registry


def _serialize_rule(rule: RuleSpec) -> dict[str, Any]:
    """Serialize a RuleSpec into a plain dict."""
    if hasattr(rule, "model_dump"):
        return rule.model_dump()
    if is_dataclass(rule):
        return asdict(rule)
    if hasattr(rule, "__dict__"):
        return dict(rule.__dict__)
    return {
        "id": getattr(rule, "id", None),
        "description": getattr(rule, "description", ""),
        "severity": getattr(rule, "severity", ""),
        "applies_to": getattr(rule, "applies_to", "all"),
    }


def _serialize_pack(pack: RulePackSpec) -> dict[str, Any]:
    """Serialize RulePackSpec into a FE-friendly metadata blob."""
    if hasattr(pack, "model_dump"):
        base = pack.model_dump()
    elif is_dataclass(pack):
        base = asdict(pack)
    elif hasattr(pack, "__dict__"):
        base = dict(pack.__dict__)
    else:
        base = {}

    base["rules"] = [_serialize_rule(r) for r in (getattr(pack, "rules", []) or [])]
    base.setdefault("name", getattr(pack, "name", None))
    base.setdefault("label", getattr(pack, "label", None))
    base.setdefault("description", getattr(pack, "description", None))
    base.setdefault("domain", getattr(pack, "domain", None))
    base.setdefault("roles", getattr(pack, "roles", []) or [])

    return base


@register_registry(name="RulePack Registry", icon="📏")
class RulePackRegistry(Registry[RulePackSpec]):
    """
    Registry for reusable rule packs (copywriting, managerial comms, FE safety, etc.)
    """

    name = "rulepack"
    description = "Registry for EVA rule packs"
    version = "1.0.0"

    def __init__(self) -> None:
        super().__init__(name=self.name)
        self._by_domain: dict[str, list[str]] = {}
        self._by_role: dict[str, list[str]] = {}

    # ---------- key helpers ----------

    def _construct_key(self, name: str) -> str:
        return (name or "").strip().lower().replace(" ", "_")

    # ---------- decorator-style registration ----------

    def register(
        self,
        name: str,
        description: str | None = None,
        version: str = "1.0.0",
        metadata: dict[str, Any] | None = None,
        pack_factory: Callable[[], RulePackSpec] | None = None,
    ) -> Callable[[Callable[[], RulePackSpec]], Callable[[], RulePackSpec]]:
        key = self._construct_key(name)

        def decorator(factory: Callable[[], RulePackSpec]) -> Callable[[], RulePackSpec]:
            spec = factory()
            if not isinstance(spec, RulePackSpec):
                AGENTVERSE_LOGGER.error(
                    LogDomain.NERV_HQ,
                    "[📏 REGISTRY][💥 RULEPACK] invalid_factory_return",
                    extra={
                        "registry_name": self._name,
                        "requested_name": name,
                        "resolved_key": key,
                        "returned_type": type(spec).__name__,
                    },
                )
                raise TypeError(
                    "RulePackRegistry.register expects a factory returning RulePackSpec"
                )

            if not spec.name:
                spec.name = key
            if not getattr(spec, "label", None):
                setattr(spec, "label", name)

            self._register_pack_spec(
                spec,
                key=key,
                version=version,
                metadata=metadata,
                description=description,
            )
            return factory

        return decorator(pack_factory) if pack_factory else decorator

    # ---------- internal registration ----------

    def _register_pack_spec(
        self,
        pack: RulePackSpec,
        *,
        key: str | None = None,
        version: str = "1.0.0",
        metadata: dict[str, Any] | None = None,
        description: str | None = None,
        replace: bool = False,
    ) -> RulePackSpec:
        key = key or self._construct_key(pack.name)

        if not replace and key in self._items:
            # Pack modules can be executed more than once when the module loader bypasses
            # sys.modules (e.g. auto_routing uses spec_from_file_location + exec_module).
            # Treat a duplicate registration of the same key as a no-op.
            return self._registry[key]

        self._store_value(key, pack)

        pack_meta = _serialize_pack(pack)
        if metadata:
            pack_meta.update(metadata)

        self._store_item(
            key,
            key=key,
            name=getattr(pack, "label", None) or pack.name,
            description=description or getattr(pack, "description", None),
            version=version,
            metadata=pack_meta,
        )
        self.registration_count += 1

        domain = getattr(pack, "domain", pack_meta.get("domain"))
        if domain:
            self._by_domain.setdefault(domain, [])
            if key not in self._by_domain[domain]:
                self._by_domain[domain].append(key)

        roles = getattr(pack, "roles", None) or pack_meta.get("roles") or []
        for role_name in roles:
            self._by_role.setdefault(role_name, [])
            if key not in self._by_role[role_name]:
                self._by_role[role_name].append(key)

        return pack

    # ---------- lookup ----------

    def get(self, name: str, version: str | None = None) -> RulePackSpec:
        key = self._construct_key(name)

        AGENTVERSE_LOGGER.info(
            LogDomain.NERV_HQ,
            "[📏 REGISTRY][🔎 RULEPACK] lookup_requested",
            extra={
                "registry_name": self._name,
                "requested_name": name,
                "resolved_key": key,
                "requested_version": version,
            },
        )

        if key not in self._registry:
            AGENTVERSE_LOGGER.warning(
                LogDomain.NERV_HQ,
                "[📏 REGISTRY][❌ RULEPACK] lookup_failed_missing",
                extra={
                    "registry_name": self._name,
                    "requested_name": name,
                    "resolved_key": key,
                },
            )
            raise KeyError(f"Rule pack '{name}' not found in {self._name} registry")

        pack = self._registry[key]
        actual_version = self._items[key].version

        if version and actual_version != version:
            AGENTVERSE_LOGGER.warning(
                LogDomain.NERV_HQ,
                "[📏 REGISTRY][⚠️ RULEPACK] version_mismatch",
                extra={
                    "registry_name": self._name,
                    "requested_name": name,
                    "resolved_key": key,
                    "requested_version": version,
                    "actual_version": actual_version,
                },
            )
            raise KeyError(
                f"Rule pack '{name}' version {version} not found (found {actual_version})"
            )

        AGENTVERSE_LOGGER.info(
            LogDomain.NERV_HQ,
            "[📏 REGISTRY][📦 RULEPACK] lookup_success",
            extra={
                "registry_name": self._name,
                "requested_name": name,
                "resolved_key": key,
                "version": actual_version,
                "pack_name": getattr(pack, "label", None) or getattr(pack, "name", None),
                "domain": getattr(pack, "domain", None),
                "roles": list(getattr(pack, "roles", []) or []),
                "rule_count": len(getattr(pack, "rules", []) or []),
            },
        )

        return pack
