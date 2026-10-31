"""Requester-scoped catalog membership for Manuscript answers.

CATALOG-0.1: the port delegates to the governed ``ResourceCatalog``, which reuses the
read policy used by ``data.validation`` (``RegistryService``'s client catalog for registry
kinds, ``WorkflowService.get_workflow`` for Workflows): a reference is accepted only if the
requester may READ it. Unknown and unreadable references are indistinguishable. A read
decision only: it grants neither attachment nor invocation. Definitions, fingerprints and
pinned sessions are unaffected; only the canonical key is returned, as before.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Protocol

from src.base.entities.user import User
from src.domains.agentverse.catalog.service import CatalogRequester, ResourceCatalog
from src.domains.agentverse.registries.base import Registry

if TYPE_CHECKING:
    from src.domains.agentverse.catalog.models import CatalogKind
    from src.domains.agentverse.services.workflow_service import WorkflowService

# Manuscript field catalog kinds (registry names) -> governed catalog kinds.
_KINDS: dict[str, CatalogKind] = {
    "agent": "agent_type",
    "role": "role",
    "personality": "soul",
    "tool": "tool",
    "workflow": "workflow",
    "rulepack": "rulepack",
}


class ManuscriptCatalog(Protocol):
    async def resolve(self, kind: str, value: str, requester: User) -> str | None:
        """The canonical key the requester may read, or ``None``."""


class GovernedManuscriptCatalog:
    """``ManuscriptCatalog`` over the governed Resource Catalog."""

    def __init__(self, catalog: ResourceCatalog) -> None:
        self._catalog = catalog

    async def resolve(self, kind: str, value: str, requester: User) -> str | None:
        catalog_kind = _KINDS.get(kind)
        if catalog_kind is None or not isinstance(requester, User):
            return None
        # The human requester alone: Manuscript answers never borrow an Agent's visibility.
        ref = await self._catalog.resolve_ref(CatalogRequester(user=requester), catalog_kind, value)
        return None if ref is None else ref.key


class RegistryManuscriptCatalog(GovernedManuscriptCatalog):
    """Compatibility constructor: a governed catalog over the given registries."""

    def __init__(
        self, *, registries: Mapping[str, Registry[Any]], workflow_service: WorkflowService
    ) -> None:
        super().__init__(ResourceCatalog(registries=registries, workflow_service=workflow_service))
