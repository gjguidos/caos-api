"""Versioned Reasoning Task Registry with exact ``task_id@version`` keys.

Follows ``ManuscriptRegistry``'s precedent (AOSA ``Registry[T]`` storage and catalog, exact
keys, pinned published fingerprints, conflict rejection, trusted ``freeze()``) without
depending on it. Registration grants nothing: whether anyone may execute a task is a UTU
decision made elsewhere (REASONING-0.1B).
"""

from __future__ import annotations

import re

from src.domains.agentverse.reasoning.models import (
    TASK_KEY_PATTERN,
    ReasoningTaskDefinition,
)
from src.domains.agentverse.registries.base import Registry, RegistryItem
from src.domains.agentverse.registries.utils.register_registry import register_registry

_KEY = re.compile(TASK_KEY_PATTERN)


class ReasoningTaskConflict(ValueError):
    """Different content under an existing or published ``task_id@version``."""


class ReasoningTaskUnavailable(KeyError):
    """No registered task under that exact key (or a different fingerprint)."""


@register_registry(name="Reasoning Task Registry", icon="🧠")
class ReasoningTaskRegistry(Registry[ReasoningTaskDefinition]):
    name = "reasoning_task_registry"
    description = "Server-owned, versioned reasoning tasks (exact task_id@version keys)"
    version = "1.0.0"

    def __init__(
        self,
        name: str = "reasoning_task_registry",
        *,
        published: dict[str, str] | None = None,
    ) -> None:
        super().__init__(name)
        self._frozen = False
        if published is None:
            from src.domains.agentverse.reasoning.tasks import PUBLISHED_FINGERPRINTS

            published = PUBLISHED_FINGERPRINTS
        self._published = dict(published)

    @property
    def frozen(self) -> bool:
        return self._frozen

    def freeze(self) -> None:
        """End trusted registration; grants nothing."""
        self._frozen = True

    def register_task(self, task: ReasoningTaskDefinition) -> ReasoningTaskDefinition:
        if self._frozen:
            raise RuntimeError("ReasoningTaskRegistry is frozen")
        if not isinstance(task, ReasoningTaskDefinition):
            raise TypeError("ReasoningTaskRegistry requires a ReasoningTaskDefinition")
        key = task.key()
        if not _KEY.fullmatch(key):
            raise ValueError(f"Invalid reasoning task key {key!r}")
        fingerprint = task.fingerprint()
        published = self._published.get(key)
        if published is not None and published != fingerprint:
            raise ReasoningTaskConflict(f"Published reasoning task {key} cannot change")
        existing = self._registry.get(key)
        if existing is not None:
            if existing.fingerprint() != fingerprint:
                raise ReasoningTaskConflict(f"Conflicting reasoning task registration for {key}")
            return existing  # Idempotent refresh (e.g. module reload).
        item = self._store_item(
            key,
            key=key,
            name=task.title,
            description=task.description or None,
            version=task.version,
            metadata={
                "id": task.id,
                "fingerprint": fingerprint,
                "model_class": task.model_class,
                "input_contract": task.input_contract,
                "output_contract": task.output_contract,
                # Server-internal resource: never a client catalog entry.
                "exposure": "internal",
                "access": "system",
            },
        )
        item.key = key  # _store_item canonicalizes; task keys are exact.
        self._store_value(key, task)
        self.registration_count += 1
        return task

    def resolve(self, key: str, fingerprint: str | None = None) -> ReasoningTaskDefinition:
        """The exact registered task; with ``fingerprint``, only that content."""
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            raise ReasoningTaskUnavailable(key)
        task = self._registry.get(key)
        if task is None or (fingerprint is not None and task.fingerprint() != fingerprint):
            raise ReasoningTaskUnavailable(key)
        return task

    def get_info(self, key: str) -> RegistryItem | None:
        return self._items.get(key)

    def resolve_key(self, value: str | None, *, kind: str) -> str:
        if value is None or value not in self._registry:
            raise ValueError(f"Unknown {kind} {value!r}")
        return value

    def __contains__(self, key: str) -> bool:
        return key in self._registry
