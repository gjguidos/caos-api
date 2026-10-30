"""Versioned Manuscript registry with exact ``id@version`` keys.

Specializes AOSA's ``Registry[T]`` like ``SynapticRegistry``: the generic stores,
metadata and catalog are reused, but keys are exact (canonicalization would turn
``id@1.0.0`` into ``id100``) and registration is guarded: a published version can never
be replaced by different content, published fingerprints are pinned in source, and
trusted bootstrap may ``freeze()`` the registry. Registration grants nothing.
"""

from __future__ import annotations

import re
from typing import Any

from src.domains.agentverse.manuscripts.models import ManuscriptDefinition
from src.domains.agentverse.registries.base import Registry, RegistryItem
from src.domains.agentverse.registries.utils.register_registry import register_registry

_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}@(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")

# Published versions are immutable: their fingerprint is recorded here when published
# and checked on every registration, so content can never change silently.
PUBLISHED_FINGERPRINTS: dict[str, str] = {
    "agent_creation_interview@1.0.0": (
        "4e04558fd1e99483a587e959a3e26ba33ef942625bf22028e1326c83e4260b38"
    ),
}


class ManuscriptConflict(ValueError):
    """Different content under an existing or published ``id@version``."""


class ManuscriptUnavailable(KeyError):
    """No registered Manuscript under that exact key (or a different fingerprint)."""


@register_registry(name="Manuscript Registry", icon="📜")
class ManuscriptRegistry(Registry[ManuscriptDefinition]):
    name = "manuscript_registry"
    description = "Versioned, immutable interview protocols (exact id@version keys)"
    version = "1.0.0"

    def __init__(
        self,
        name: str = "manuscript_registry",
        *,
        published: dict[str, str] | None = None,
    ) -> None:
        super().__init__(name)
        self._frozen = False
        self._published = dict(PUBLISHED_FINGERPRINTS if published is None else published)

    @property
    def frozen(self) -> bool:
        return self._frozen

    def freeze(self) -> None:
        """End trusted registration; grants nothing."""
        self._frozen = True

    def register_definition(self, definition: ManuscriptDefinition) -> ManuscriptDefinition:
        if self._frozen:
            raise RuntimeError("ManuscriptRegistry is frozen")
        if not isinstance(definition, ManuscriptDefinition):
            raise TypeError("ManuscriptRegistry requires a ManuscriptDefinition")
        key = definition.key()
        fingerprint = definition.fingerprint()
        published = self._published.get(key)
        if published is not None and published != fingerprint:
            raise ManuscriptConflict(f"Published Manuscript {key} cannot change")
        existing = self._registry.get(key)
        if existing is not None:
            if existing.fingerprint() != fingerprint:
                raise ManuscriptConflict(f"Conflicting Manuscript registration for {key}")
            return existing  # Idempotent refresh (e.g. module reload).
        item = self._store_item(
            key,
            key=key,
            name=definition.title,
            description=definition.description or None,
            version=definition.version,
            metadata={"id": definition.id, "fingerprint": fingerprint, "access": "system"},
        )
        item.key = key  # _store_item canonicalizes; Manuscript keys are exact.
        self._store_value(key, definition)
        self.registration_count += 1
        return definition

    def resolve(self, key: str, fingerprint: str | None = None) -> ManuscriptDefinition:
        """The exact registered definition; with ``fingerprint``, only that content."""
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            raise ManuscriptUnavailable(key)
        definition = self._registry.get(key)
        if definition is None or (
            fingerprint is not None and definition.fingerprint() != fingerprint
        ):
            raise ManuscriptUnavailable(key)
        return definition

    def get_info(self, key: str) -> RegistryItem | None:
        return self._items.get(key)

    def resolve_key(self, value: str | None, *, kind: str) -> str:
        if value is None or value not in self._registry:
            raise ValueError(f"Unknown {kind} {value!r}")
        return value

    def __contains__(self, key: str) -> bool:
        return key in self._registry

    def outlines(self) -> list[dict[str, Any]]:
        return [self._registry[key].outline() for key in sorted(self._registry)]


manuscript_registry_instance = ManuscriptRegistry()
