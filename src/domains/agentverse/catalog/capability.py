"""``catalog.project@1.0.0``: Enki's read-only, requester-scoped catalog (CATALOG-0.1A).

Approved for Enki only (reviewed local UTU policy), provider ``system.catalog``, no trusted
line. Executable only under its own single-signal attenuated scope, so an unscoped client
(including the generic signal route) or another capability's scope cannot reach it. The
caller must be an Agent in a live human session: tenant, human requester and source Agent
come only from the CPBX-issued, session-origin permit and the trusted session adapter,
re-verified against current UTU authority before and after the read. Bootstrap permits
are refused.

The result is exactly the human requester's own catalog projection (``ResourceCatalog``)
for that source Agent: no visibility beyond the human's, no mutation, no execution
authority (entries always carry ``execution_authorization: not_evaluated``).
"""

from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, TypeAdapter, ValidationError

from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.catalog.models import CatalogListRequest, CatalogResolveRequest
from src.domains.agentverse.catalog.service import CatalogRequester, ResourceCatalog
from src.domains.agentverse.ncsc.cpbx import AuthorizedSynapticClient, SynapticExchange
from src.domains.agentverse.ncsc.models import Synapse
from src.domains.agentverse.registries.synaptic_registry import SynapticBinding, SynapticRegistry
from src.domains.agentverse.security.capability_scope import CapabilityBinding, CapabilityScope
from src.domains.agentverse.security.revocation import ExecutionPermit
from src.domains.agentverse.security.utu import AuthorizedAgentPrincipal, UTUAuthority
from src.domains.agentverse.tools.spec import SynapticCapabilitySpec, SynapticToolSpec

SIGNAL = "catalog.project"
PROVIDER = "system.catalog"
VERSION = "1.0.0"
BINDING = CapabilityBinding(SIGNAL, PROVIDER, VERSION)
SCOPE = CapabilityScope(SIGNAL, BINDING, frozenset({BINDING}))
RESULT_CONTRACT = "caos.catalog.project_result"
# A bounded request: strictly smaller than any legitimate list/resolve command needs.
MAX_PAYLOAD_BYTES = 16 * 1024


class _Projection(BaseModel):
    """Identity-free: no tenant, requester, Agent, permit, grant, provider or signal."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    contract: Literal["caos.catalog.project"]
    schema_version: StrictInt = Field(ge=1, le=1)


class ListProjection(_Projection):
    operation: Literal["list"]
    request: CatalogListRequest


class ResolveProjection(_Projection):
    operation: Literal["resolve"]
    request: CatalogResolveRequest


ProjectionCommand = Annotated[ListProjection | ResolveProjection, Field(discriminator="operation")]
_COMMAND: TypeAdapter[ListProjection | ResolveProjection] = TypeAdapter(ProjectionCommand)


def parse_command(payload: Any) -> ListProjection | ResolveProjection:
    """Strict JSON-mode validation (tuples arrive as arrays); bounded before parsing."""
    if not isinstance(payload, dict):
        raise TypeError("Catalog command must be an object")
    raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    if len(raw.encode()) > MAX_PAYLOAD_BYTES:
        raise ValueError("Catalog command too large")
    return _COMMAND.validate_json(raw)


class CatalogCapability:
    def __init__(self, *, authority: UTUAuthority, catalog: ResourceCatalog) -> None:
        self._authority = authority
        self._catalog = catalog

    async def project(self, synapse: Synapse, client: AuthorizedSynapticClient) -> dict[str, Any]:
        permit = await self._authorize(synapse, client)
        try:
            command = parse_command(synapse.payload)
        except (ValidationError, ValueError, TypeError):
            return self._failure("catalog.invalid_request")
        # The human is resolved from the trusted session adapter, never the payload.
        user = await self._authority.resource_requester(permit)
        requester = CatalogRequester(user=user, source_agent_id=permit.key.agent_id)
        if isinstance(command, ListProjection):
            result = (await self._catalog.list(requester, command.request)).model_dump(mode="json")
        else:
            result = (await self._catalog.resolve(requester, command.request)).model_dump(
                mode="json"
            )
        # Never release a result to a caller whose authority ended meanwhile.
        await self._authority.check_permit(permit)
        return {
            "contract": RESULT_CONTRACT,
            "ok": True,
            "operation": command.operation,
            "result": result,
        }

    async def _authorize(
        self, synapse: Synapse, client: AuthorizedSynapticClient
    ) -> ExecutionPermit:
        """Independent provider-side verification of the exact scoped, session-origin caller."""
        permit = client.execution_permit if isinstance(client, AuthorizedSynapticClient) else None
        if (
            not isinstance(permit, ExecutionPermit)
            or (synapse.signal, synapse.target, synapse.tenant_id, synapse.source)
            != (SIGNAL, PROVIDER, permit.key.tenant_id, permit.key.agent_id)
            or permit.scope != SCOPE
            or not permit.binding_chain
            or permit.binding_chain[-1] != BINDING
            or synapse.entry_operation != SCOPE.operation
            or synapse.provider_version != VERSION
            or permit.origin != "session"
        ):
            raise EventPermissionError(f"{SIGNAL} requires its own verified scoped caller")
        await self._authority.check_permit(permit)
        current = await self._authority.resolve_agent_principal(permit.key)
        if (
            current.permit.versions != permit.versions
            or SIGNAL not in self._authority.grants_for(current).invoke
        ):
            raise EventPermissionError("Catalog authority changed")
        return permit

    @staticmethod
    def _failure(code: str) -> dict[str, Any]:
        return {"contract": RESULT_CONTRACT, "ok": False, "code": code, "retryable": False}


def register_catalog_capability(
    registry: SynapticRegistry, capability: CatalogCapability
) -> SynapticBinding:
    """Trusted bootstrap registration of the exact signal. Grants nothing."""
    return registry.register(
        SIGNAL,
        description="Read-only, requester-scoped catalog projection for the caller's own human",
        version=VERSION,
        metadata={
            "access": "system",
            "deterministic": True,
            "read_only": True,
            "synaptic": SynapticToolSpec(
                capabilities=[SynapticCapabilitySpec(signal=SIGNAL, access="system")]
            ).model_dump(),
        },
        component=SynapticBinding(SIGNAL, PROVIDER, capability.project, VERSION),
    )


def catalog_policy_state(policy: Any) -> bool:
    """Whether the reviewed policy enables ``catalog.project``; trusted lines are refused."""
    if any(line.target == SIGNAL for line in policy._trusted_lines):
        raise ValueError(f"{SIGNAL} is approved without trusted lines")
    return any(SIGNAL in grant.invoke for grant in policy._grants.values())


def validate_catalog_binding(registry: SynapticRegistry) -> None:
    """Fail closed at trusted startup when the policy grants ``catalog.project``."""
    try:
        binding = registry.resolve(SIGNAL, VERSION)
    except KeyError as exc:
        raise ValueError(f"Missing exact binding: {SIGNAL}@{VERSION}") from exc
    if (binding.provider, binding.version) != (PROVIDER, VERSION):
        raise ValueError(f"{SIGNAL} binding provider/version mismatch")


async def project_catalog(
    authority: UTUAuthority,
    exchange: SynapticExchange,
    principal: AuthorizedAgentPrincipal,
    command: dict[str, Any],
) -> dict[str, Any]:
    """Trusted in-process caller for an Agent runtime (Enki's future turn plan): narrows
    the verified principal to exactly ``catalog.project`` before binding a client."""
    scoped = await authority.attenuate(principal, SCOPE)
    client = exchange.bind(scoped)  # NEVER bind the unattenuated principal.
    try:
        result = await client.request(SIGNAL, command, target=PROVIDER, version=VERSION)
    finally:
        client.revoke()
        await client.drain()
    return result  # The provider's own ``caos.catalog.project_result`` envelope.
