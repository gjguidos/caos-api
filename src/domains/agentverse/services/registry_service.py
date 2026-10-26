from __future__ import annotations

import builtins
from typing import Any, Literal

from src.base.entities.user import User
from src.domains.agentverse.registries.base import Registry, RegistryItem
from pydantic import ValidationError

from src.domains.agentverse.entities.resource_exposure import classify_resource
from src.domains.agentverse.entities.resource_ownership import ownership_status, verified_tenant
from src.domains.agentverse.security.manifest_projection import project_metadata
from src.domains.agentverse.security.resource_references import (
    SCHEMA_FIELDS,
    direct_references,
    nested_nodes,
    reference_key,
)
from src.domains.agentverse.security.utu import UTUAuthority


class RegistryService:
    """
    Registry facade. HTTP callers use the principal-aware *_for_client methods.
    Raw list/get_info/get_component are trusted runtime/bootstrap interfaces.
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
        **kwargs: Any,
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

    CLIENT_KINDS = frozenset({"agent", "personality", "tool", "role", "rulepack", "workflow"})

    def _client_registry(self, which: str) -> Registry[Any]:
        if which not in self.CLIENT_KINDS:
            raise KeyError("Registry unavailable")
        return self._get_registry(which)

    def client_metadata_allowed(
        self,
        metadata: dict[str, Any],
        user: User | None,
        *,
        operation: Literal["DISCOVER", "READ"] = "READ",
        seen: frozenset[tuple[str, str]] = frozenset(),
        depth: int = 0,
        _ownership_context: dict[str, Any] | None = None,
        _reference_projection: bool = False,
    ) -> bool:
        """Reject whole manifests with protected nested resources/references.

        Walk serialized metadata only; never instantiate a provider to authorize it.
        Cycles are bounded; every referenced item is checked before revisiting it.
        """
        inherited = _ownership_context or {}
        if any(field in metadata for field in ("ownership", "tenant_id", "tenant_scoped")) or (
            "metadata" in metadata and ("key" in metadata or "id" in metadata)
        ):
            # Inline resource declarations are independent records. A legacy
            # declaration cannot borrow ownership from its enclosing manifest.
            inherited = {}
        policy = {**inherited, **metadata}
        if depth > 32 or not UTUAuthority.can_access_resource(user, policy, operation=operation):
            return False
        ownership_context: dict[str, Any] = {
            field: policy[field]
            for field in ("ownership", "tenant_id", "tenant_scoped")
            if field in policy
        }

        def reference(kind: str, value: Any) -> bool:
            key = reference_key(value)
            if not isinstance(key, str) or not key.strip():
                return False
            reg = self._registries.get(kind)
            item = reg.get_info(key) if reg is not None else None
            if item is None or not UTUAuthority.can_access_resource(
                user, item.metadata, operation="READ"
            ):
                return False
            marker = (kind, item.key)
            if _reference_projection:
                if marker in seen:
                    return True
                try:
                    self._project_item(kind, item, user, seen=seen)
                except KeyError:
                    return False
                return True
            return marker in seen or self.client_metadata_allowed(
                item.metadata, user, seen=seen | {marker}, depth=depth + 1
            )

        if not all(reference(kind, value) for kind, value in direct_references(metadata)):
            return False
        for field, value in metadata.items():
            if field in SCHEMA_FIELDS:
                continue
            # Inline expansions also occur inside nested arrays.
            for entry in nested_nodes(value, depth=depth):
                if not self.client_metadata_allowed(
                    entry, user, seen=seen, depth=depth + 1,
                    _ownership_context=ownership_context,
                    _reference_projection=_reference_projection,
                ):
                    return False
        return True

    def _dependencies_valid(
        self, metadata: dict[str, Any], user: User | None,
        *, seen: frozenset[tuple[str, str]] = frozenset(), depth: int = 0,
    ) -> bool:
        """Validate operative composition, without granting client read authority.

        A registered system/internal dependency may satisfy composition, but is
        never serialized here. Unknown ownership and foreign tenants still fail.
        Optional absent tools are not requirements; present optional references
        must be valid. Schemas carry no operative dependency authority.
        """
        if depth > 32:
            return False
        for kind, value in direct_references(metadata):
            key = reference_key(value)
            reg = self._registries.get(kind)
            item = reg.get_info(key) if reg is not None and isinstance(key, str) and key.strip() else None
            optional = metadata.get("optional_tools")
            if item is None and isinstance(optional, dict) and key in optional:
                # Only this map's optional declaration can be absent. A duplicate
                # required reference in the same node is still checked below.
                required_node = {k: v for k, v in metadata.items() if k != "optional_tools"}
                if (kind, value) not in list(direct_references(required_node)):
                    continue
            if item is None:
                return False
            policy = item.metadata
            try:
                classify_resource(policy)  # invalid exposure remains invalid composition
            except (ValueError, TypeError, AttributeError):
                return False
            status = ownership_status(policy)
            if status not in ("global", "system", "tenant"):
                return False
            if status == "tenant" and verified_tenant(user) != policy["tenant_id"]:
                return False
            # Validate private ownership using UTU's existing identity policy.
            # This constructed scope check is never used as a client-read grant.
            scope_policy = {k: policy[k] for k in ("tenant_id", "tenant_scoped", "creator", "created_by", "whitelisted_groups") if k in policy}
            scope_policy["ownership"] = "global" if status == "system" else status
            if policy.get("visibility") == "private":
                scope_policy["visibility"] = "private"
            elif policy.get("visibility") not in (None, "public", "hidden"):
                return False
            if not UTUAuthority.can_access_resource(user, scope_policy, operation="READ"):
                return False
            marker = (kind, item.key)
            if marker not in seen and not self._dependencies_valid(policy, user, seen=seen | {marker}, depth=depth + 1):
                return False
        for field, value in metadata.items():
            if field in SCHEMA_FIELDS or field == "ui":
                continue
            for entry in nested_nodes(value, depth=depth):
                try:
                    if classify_resource(entry).exposure != "public":
                        return False
                except (ValueError, TypeError, AttributeError):
                    return False
                # Independent inline resource policy cannot borrow parent scope.
                if any(k in entry for k in ("ownership", "tenant_id", "tenant_scoped")):
                    if not UTUAuthority.can_access_resource(user, entry, operation="READ"):
                        return False
                if not self._dependencies_valid(entry, user, seen=seen, depth=depth + 1):
                    return False
        return True

    def _project_item(
        self, which: str, item: RegistryItem, user: User | None,
        *, operation: Literal["DISCOVER", "READ"] = "READ",
        seen: frozenset[tuple[str, str]] = frozenset(),
    ) -> RegistryItem:
        # No request metadata, inclusion flag or pre-projected payload can opt in.
        if not UTUAuthority.can_access_resource(user, item.metadata, operation=operation):
            raise KeyError("Entry unavailable")
        if not self._dependencies_valid(item.metadata, user, seen=seen | {(which, item.key)}):
            raise KeyError("Entry unavailable")

        def readable(kind: str, key: str) -> bool:
            marker = (kind, key)
            if marker == (which, item.key):
                return UTUAuthority.can_access_resource(user, item.metadata, operation="READ")
            if marker in seen or len(seen) > 32:
                return False
            reg = self._registries.get(kind)
            target = reg.get_info(key) if reg is not None else None
            if target is None:
                return False
            try:
                self._project_item(kind, target, user, seen=seen | {(which, item.key)})
            except (KeyError, ValidationError, TypeError, ValueError):
                return False
            return True

        try:
            projected = project_metadata(
                which, item.metadata, readable,
                lambda node: self.client_metadata_allowed(
                    node, user, seen=seen | {(which, item.key)},
                    _ownership_context={field: item.metadata[field] for field in ("ownership", "tenant_id", "tenant_scoped") if field in item.metadata},
                    _reference_projection=True,
                ),
                lambda node: self._dependencies_valid(node, user, seen=seen | {(which, item.key)}),
            )
        except (ValidationError, TypeError, ValueError, AttributeError):
            raise KeyError("Entry unavailable") from None
        # Recheck the actual client payload with the original strict UTU walker.
        if not self.client_metadata_allowed(projected, user, seen=seen | {(which, item.key)}, _reference_projection=True):
            raise KeyError("Entry unavailable")
        return RegistryItem(
            key=item.key, name=item.name, description=item.description,
            version=item.version, metadata=projected, registration_time=item.registration_time,
        )

    def list_for_client(
        self, which: str, user: User | None, *, include_metadata: bool = False
    ) -> builtins.list[Any]:
        reg = self._client_registry(which)
        out: builtins.list[Any] = []
        for item in reg.items():
            try:
                projected = self._project_item(which, item, user, operation="DISCOVER")
            except KeyError:
                continue
            if not include_metadata:
                out.append(projected.key)
            elif which == "tool":
                # Preserve the existing flattened tool catalog envelope.
                out.append({"key": projected.key, "name": projected.name,
                            "description": projected.description or "", "version": projected.version,
                            **projected.metadata})
            else:
                out.append(projected.model_dump())
        return out

    def get_info_for_client(self, which: str, key: str, user: User | None) -> RegistryItem:
        reg = self._client_registry(which)
        item = reg.get_info(key)
        if item is None:
            raise KeyError("Entry unavailable")
        return self._project_item(which, item, user)
