"""``store.create``: governed, deterministic persistence of approved resources.

CPBX/UTU decide who may invoke it (registration grants nothing). Authority comes only
from the CPBX-issued execution permit, never from the payload, an agent ID or a name,
and is rechecked at the write boundary. The tool selects an approved store by logical
resource type; collections, filters, operators and connections are not expressible.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.logging.logger_v1 import AGENTVERSE_LOGGER, LogDomain
from src.domains.agentverse.ncsc.models import Synapse
from src.domains.agentverse.operations.admission import permit_provenance
from src.domains.agentverse.registries.synaptic_registry import SynapticBinding, SynapticRegistry
from src.domains.agentverse.security.revocation import ExecutionPermit
from src.domains.agentverse.tools.base import ToolResult
from src.domains.agentverse.tools.store_tool.models import (
    InvalidStorePayload,
    ProvisionedIdentity,
    ResourceStore,
    StoreCreateRequest,
    StoreError,
    StoreProvisionedCreateRequest,
    StoreWriteContext,
    UnsupportedResource,
)
from src.domains.agentverse.tools.store_tool.spec import (
    STORE_CREATE_PROVISIONED_SIGNAL,
    STORE_CREATE_PROVISIONED_VERSION,
    STORE_CREATE_SIGNAL,
    STORE_CREATE_VERSION,
    STORE_PROVIDER,
    STORE_TOOL_SPEC,
)

if TYPE_CHECKING:
    from src.domains.agentverse.ncsc.cpbx import AuthorizedSynapticClient
    from src.domains.agentverse.security.utu import UTUAuthority


class StoreTool:
    def __init__(self, *, stores: Mapping[str, ResourceStore], authority: UTUAuthority) -> None:
        self._stores = stores
        self._authority = authority

    async def create(self, synapse: Synapse, client: AuthorizedSynapticClient) -> dict[str, Any]:
        context = await self._write_context(synapse, client)
        try:
            request = self._request(synapse.payload)
        except StoreError as exc:
            return self._failure(exc)
        return await self._create(
            request.resource_type,
            request.payload,
            idempotency_key=request.idempotency_key,
            context=context,
        )

    async def _create(
        self,
        resource_type: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str,
        context: StoreWriteContext,
    ) -> dict[str, Any]:
        try:
            store = self._stores.get(resource_type)
            if store is None:
                raise UnsupportedResource("Unsupported resource type")
            result = await store.create(payload, idempotency_key=idempotency_key, context=context)
        except StoreError as exc:
            return self._failure(exc)
        AGENTVERSE_LOGGER.info(
            LogDomain.EXISTENTIAL_INDEX,
            "[🧠 AEI][💾 STORE] store_create",
            extra={
                "resource_type": result.resource_type,
                "resource_id": result.resource_id,
                "created": result.created,
                "tenant_id": context.provenance.tenant_id,
                "principal_id": context.provenance.principal_id,
            },
        )
        return ToolResult(success=True, result=result.model_dump(mode="json")).model_dump()

    @staticmethod
    def _failure(exc: StoreError) -> dict[str, Any]:
        return ToolResult(
            success=False,
            error=str(exc),
            meta={"code": exc.code, "retryable": exc.retryable},
        ).model_dump()

    async def _write_context(
        self,
        synapse: Synapse,
        client: AuthorizedSynapticClient,
        *,
        signal: str = STORE_CREATE_SIGNAL,
    ) -> StoreWriteContext:
        """Tenant and subject come from the CPBX-issued permit; missing authority denies."""
        permit = client.execution_permit
        if not isinstance(permit, ExecutionPermit) or (
            permit.key.tenant_id,
            permit.key.agent_id,
        ) != (synapse.tenant_id, synapse.source):
            raise EventPermissionError(f"{signal} requires a UTU execution permit")
        await self._authority.check_permit(permit)

        async def verify() -> None:
            await self._authority.check_permit(permit)

        return StoreWriteContext(provenance=permit_provenance(permit, signal), verify=verify)

    @staticmethod
    def _request(payload: dict[str, Any]) -> StoreCreateRequest:
        try:
            return StoreCreateRequest.model_validate(payload)
        except ValidationError as exc:
            if any(error["loc"][:1] == ("resource_type",) for error in exc.errors()):
                raise UnsupportedResource("Unsupported resource type") from exc
            raise InvalidStorePayload("Invalid store.create request") from exc


class ProvisionedStoreCapability:
    """``store.create.provisioned``: create only identities pinned in server policy.

    The caller names a slot; policy decides the agent ID and which principal, subject
    and tenant may create it. An unknown slot or any permit mismatch is a denial,
    raised before any read or write. The write itself is the ordinary tenant-owned
    ``store.create`` path (idempotency, ownership derivation, write-boundary recheck).
    """

    def __init__(self, *, tool: StoreTool, provisions: Mapping[str, ProvisionedIdentity]) -> None:
        self._tool = tool
        self._provisions = MappingProxyType(dict(provisions))

    async def create(self, synapse: Synapse, client: AuthorizedSynapticClient) -> dict[str, Any]:
        context = await self._tool._write_context(
            synapse, client, signal=STORE_CREATE_PROVISIONED_SIGNAL
        )
        try:
            request = StoreProvisionedCreateRequest.model_validate(synapse.payload)
        except ValidationError:
            return self._tool._failure(
                InvalidStorePayload("Invalid store.create.provisioned request")
            )
        entry = self._provisions.get(request.slot)
        provenance = context.provenance
        if entry is None or (
            entry.resource_type,
            entry.tenant_id,
            entry.principal_id,
            entry.subject_id,
        ) != (
            request.resource_type,
            provenance.tenant_id,
            provenance.principal_id,
            provenance.subject_id,
        ):
            raise EventPermissionError("Provisioned identity denied")
        return await self._tool._create(
            entry.resource_type,
            request.payload,
            idempotency_key=f"provisioned:{entry.slot}",
            context=replace(context, provisioned=entry),
        )


def register_store_create(registry: SynapticRegistry, tool: StoreTool) -> SynapticBinding:
    """Trusted bootstrap registration. Registration grants no invocation permission."""
    return registry.register(
        STORE_CREATE_SIGNAL,
        description="Idempotent creation of an approved resource through a trusted store",
        version=STORE_CREATE_VERSION,
        metadata={
            "access": "system",
            "deterministic": True,
            "synaptic": STORE_TOOL_SPEC.model_dump(),
        },
        component=SynapticBinding(
            STORE_CREATE_SIGNAL, STORE_PROVIDER, tool.create, STORE_CREATE_VERSION
        ),
    )


def register_store_create_provisioned(
    registry: SynapticRegistry, capability: ProvisionedStoreCapability
) -> SynapticBinding:
    """Trusted bootstrap registration. Registration grants no invocation permission."""
    return registry.register(
        STORE_CREATE_PROVISIONED_SIGNAL,
        description="Idempotent creation of a policy-pinned tenant resource identity",
        version=STORE_CREATE_PROVISIONED_VERSION,
        metadata={
            "access": "system",
            "deterministic": True,
            "synaptic": STORE_TOOL_SPEC.model_dump(),
        },
        component=SynapticBinding(
            STORE_CREATE_PROVISIONED_SIGNAL,
            STORE_PROVIDER,
            capability.create,
            STORE_CREATE_PROVISIONED_VERSION,
        ),
    )
