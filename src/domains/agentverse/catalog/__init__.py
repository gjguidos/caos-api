"""CATALOG-0.1: governed, requester-aware Resource Catalog (read-only, in-process).

The catalog is a projection over the authoritative AOSA registries, the Workflow catalog
and the Manuscript Registry. It owns no resource truth, writes nothing and grants nothing:
existence, requester visibility, candidate compatibility, candidate attachment and
execution authorization are reported as separate decisions, and the last is never
evaluated here.
"""

from src.domains.agentverse.catalog.models import (
    CATALOG_CONTRACT_VERSION,
    CatalogCandidate,
    CatalogEntry,
    CatalogKind,
    CatalogListRequest,
    CatalogListResult,
    CatalogResolution,
    CatalogResolveRequest,
    CatalogResourceRef,
)
from src.domains.agentverse.catalog.service import CatalogRequester, ResourceCatalog

__all__ = [
    "CATALOG_CONTRACT_VERSION",
    "CatalogCandidate",
    "CatalogEntry",
    "CatalogKind",
    "CatalogListRequest",
    "CatalogListResult",
    "CatalogRequester",
    "CatalogResolution",
    "CatalogResolveRequest",
    "CatalogResourceRef",
    "ResourceCatalog",
]
