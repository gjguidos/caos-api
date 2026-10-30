"""Requester-scoped catalog membership for Manuscript answers.

Reuses the existing read policy used by ``data.validation`` (``RegistryService``'s client
catalog for registry kinds, ``WorkflowService.get_workflow`` for Workflows): a reference
is accepted only if the requester may READ it. Unknown and unreadable references are
indistinguishable. A read decision only: it grants neither attachment nor invocation.
CATALOG-0.1 may replace this port with its governed capability.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Protocol

from src.base.entities.user import User
from src.domains.agentverse.entities.workflow import slugify_workflow_key
from src.domains.agentverse.exceptions import WorkflowNotFoundError
from src.domains.agentverse.registries.base import Registry
from src.domains.agentverse.services.registry_service import RegistryService

if TYPE_CHECKING:
    from src.domains.agentverse.services.workflow_service import WorkflowService


class ManuscriptCatalog(Protocol):
    async def resolve(self, kind: str, value: str, requester: User) -> str | None:
        """The canonical key the requester may read, or ``None``."""


class RegistryManuscriptCatalog:
    def __init__(
        self, *, registries: Mapping[str, Registry[Any]], workflow_service: WorkflowService
    ) -> None:
        self._registries = registries
        self._catalog = RegistryService(dict(registries))
        self._workflows = workflow_service

    async def resolve(self, kind: str, value: str, requester: User) -> str | None:
        if kind == "workflow":
            key = slugify_workflow_key(value)
            try:
                await self._workflows.get_workflow(key, requester)
            except WorkflowNotFoundError:
                return None
            return key
        registry = self._registries.get(kind)
        if registry is None:
            return None
        try:
            key = registry.resolve_key(value, kind=kind)
            self._catalog.get_info_for_client(kind, key, requester)
        except (KeyError, ValueError):
            return None
        return key
