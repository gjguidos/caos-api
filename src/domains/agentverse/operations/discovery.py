"""``operations.dispatch.discover``: pending-work discovery as a registered capability.

CPBX/UTU decide who may invoke it; results are always scoped to the caller's trusted
tenant, never a payload-supplied one. Trusted bootstrap (the future dispatcher) calls
the same repository method directly so discovery never depends on an Enlil-managed
operation existing first (Principle 1/15 kernel exception: no circular dependency).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.base.operations.v2.admission import DiscoveryRequest, OperationAdmissionRepository
from src.domains.agentverse.ncsc.models import Synapse
from src.domains.agentverse.registries.synaptic_registry import SynapticBinding, SynapticRegistry

if TYPE_CHECKING:
    from src.domains.agentverse.ncsc.cpbx import AuthorizedSynapticClient

DISCOVERY_SIGNAL = "operations.dispatch.discover"
DISCOVERY_PROVIDER = "system.enlil"
DISCOVERY_VERSION = "1.0.0"


class DispatchDiscoveryCapability:
    """Deterministic, read-only and bounded; returns references, not inputs or authority."""

    def __init__(self, repository: OperationAdmissionRepository) -> None:
        self._repository = repository

    async def discover(self, synapse: Synapse, client: AuthorizedSynapticClient) -> dict[str, Any]:
        request = DiscoveryRequest.model_validate(synapse.payload)
        page = await self._repository.discover(request, tenant_id=synapse.tenant_id)
        return page.model_dump(mode="json")


def register_dispatch_discovery(
    registry: SynapticRegistry, repository: OperationAdmissionRepository
) -> SynapticBinding:
    """Trusted bootstrap registration. Registration grants no invocation permission."""
    capability = DispatchDiscoveryCapability(repository)
    return registry.register(
        DISCOVERY_SIGNAL,
        description="Bounded discovery of pending admitted operations in the caller's tenant",
        version=DISCOVERY_VERSION,
        metadata={"access": "system", "deterministic": True},
        component=SynapticBinding(
            DISCOVERY_SIGNAL, DISCOVERY_PROVIDER, capability.discover, DISCOVERY_VERSION
        ),
    )
