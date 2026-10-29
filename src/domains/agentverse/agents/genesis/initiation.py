"""Local G1.4 authority handoff, not an orchestrator or principal impersonation.

CPBX keeps JOSHUA's principal on entry. Only this trusted adapter may resolve the
separately approved Genesis identity, for the same live session and user, using a
server-pinned handoff. No permit or client is returned to the initiating agent.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.base.events.v2.contracts import EventPermissionError
from src.base.operations.v2.admission import sha256_hex
from src.domains.agentverse.agents.genesis import GenesisProvisioner, genesis_operation_id
from src.domains.agentverse.agents.genesis.models import (
    GenesisAuditError,
    GenesisOperationInProgress,
    GenesisProvisionRequest,
)
from src.domains.agentverse.audit.models import LogEvent, stable_event_id
from src.domains.agentverse.audit.repository import LogError
from src.domains.agentverse.audit.service import AuditSink
from src.domains.agentverse.ncsc.cpbx import AuthorizedSynapticClient, SynapticExchange
from src.domains.agentverse.ncsc.models import Synapse, SynapticExecutionContext
from src.domains.agentverse.operations.admission import permit_provenance
from src.domains.agentverse.registries.synaptic_registry import SynapticBinding, SynapticRegistry
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey
from src.domains.agentverse.security.revocation import ExecutionPermit
from src.domains.agentverse.security.utu import UTUAuthority
from src.domains.agentverse.tools.spec import SynapticCapabilitySpec, SynapticToolSpec
from src.domains.agentverse.tools.store_tool.models import ProvisionedIdentity

SIGNAL = "genesis.enki.request"
VERSION = "1.0.0"
PROVIDER = "system.genesis"
INITIATION_EVENT = "genesis.initiation.authorized"
PolicyId = Annotated[str, Field(min_length=1, max_length=256, pattern=r"^\S+$")]


class GenesisHandoff(BaseModel):
    """Trusted deployment binding. It cannot be selected or changed by a caller."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: PolicyId
    subject_id: PolicyId
    initiator_id: PolicyId
    executor_id: PolicyId
    agent_id: PolicyId
    slot: Literal["enki"]

    @model_validator(mode="after")
    def distinct_identities(self) -> GenesisHandoff:
        if len({self.initiator_id, self.executor_id, self.agent_id}) != 3:
            raise ValueError("Genesis handoff identities must be distinct")
        return self


class EnkiInitiationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    # Strict creation validation remains data.validation's responsibility. The
    # envelope cannot carry a slot, identity, authority, or provenance override.
    creation: dict[str, Any]


class GenesisInitiation:
    def __init__(
        self,
        *,
        handoffs: tuple[GenesisHandoff, ...],
        provisions: Mapping[str, ProvisionedIdentity],
        authority: UTUAuthority,
        exchange: SynapticExchange,
        provisioner: GenesisProvisioner,
        log: AuditSink,
    ) -> None:
        self._handoffs = {(h.tenant_id, h.initiator_id, h.subject_id): h for h in handoffs}
        if len(self._handoffs) != len(handoffs):
            raise ValueError("Duplicate Genesis handoff")
        for h in handoffs:
            p = provisions.get(h.slot)
            if p is None or (p.tenant_id, p.principal_id, p.subject_id, p.agent_id) != (
                h.tenant_id,
                h.executor_id,
                h.subject_id,
                h.agent_id,
            ):
                raise ValueError("Genesis handoff differs from the provisioned identity")
        self._authority = authority
        self._provisions = MappingProxyType(dict(provisions))
        self._exchange = exchange
        self._provisioner = provisioner
        self._log = log

    async def handle(self, synapse: Synapse, client: AuthorizedSynapticClient) -> dict[str, Any]:
        body = EnkiInitiationRequest.model_validate(synapse.payload)
        permit = client.execution_permit if isinstance(client, AuthorizedSynapticClient) else None
        if not isinstance(permit, ExecutionPermit) or (
            synapse.signal,
            synapse.target,
            synapse.tenant_id,
            synapse.source,
        ) != (SIGNAL, PROVIDER, permit.key.tenant_id, permit.key.agent_id):
            raise EventPermissionError("Genesis initiation requires its verified CPBX caller")
        key = permit.key
        handoff = self._handoffs.get((key.tenant_id, key.agent_id, key.user_id))
        if handoff is None:
            raise EventPermissionError("Genesis handoff denied")
        # Re-read current approvals, definitions, membership and authority, even
        # for terminal replay. A once-issued permit is not a current approval.
        initiator = await self._authority.resolve_agent_principal(key)
        if initiator.permit != permit:
            raise EventPermissionError("Genesis initiating permit changed")
        self._authority.require_invoke(
            context=SynapticExecutionContext(
                principal_id=key.agent_id, tenant_id=key.tenant_id, permit=permit
            ),
            signal=SIGNAL,
        )
        executor = await self._authority.resolve_agent_principal(
            AgentInstanceKey(key.tenant_id, key.user_id, handoff.executor_id, key.session_id)
        )
        if self._authority.grants_for(initiator).invoke != frozenset({SIGNAL}) or (
            self._authority.grants_for(executor).invoke
            != frozenset({"data.validation", "store.create.provisioned"})
        ):
            raise EventPermissionError("Genesis handoff grants differ from the reviewed policy")
        pinned = self._provisions[handoff.slot]
        if (body.creation.get("name"), body.creation.get("type")) != (pinned.name, pinned.type):
            # Guard BEFORE opening/replaying an operation: a historical chat
            # success is not approval to recreate the now form-pinned identity.
            raise EventPermissionError("Genesis request differs from the current pinned definition")
        profile = (body.creation.get("role_name"), body.creation.get("personality_key"))
        if pinned.type == "form" and profile != ("enki", "empty_eva"):
            raise EventPermissionError("Genesis request differs from the reviewed Enki composition")
        request = GenesisProvisionRequest(slot=handoff.slot, creation=body.creation)
        operation_id = genesis_operation_id(executor.permit, request)
        correlation = str(operation_id)
        authorization = permit_provenance(permit, SIGNAL)
        # One fact per operation + verified initiating authorization, not per
        # synapse/message. Re-authentication creates a distinct authorization fact;
        # retrying the same authorization reuses its ID and canonical content.
        event = LogEvent(
            event_id=stable_event_id(
                correlation_id=correlation,
                event_type=INITIATION_EVENT,
                step=authorization.evidence_digest,
            ),
            event_type=INITIATION_EVENT,
            outcome="succeeded",
            subject={"resource_type": "agent", "resource_id": handoff.agent_id},
            target={"signal": SIGNAL, "version": VERSION},
            correlation={
                "operation_id": operation_id,
                "correlation_id": correlation,
                "causation_id": correlation,
            },
            metadata={
                "event_version": 1,
                "slot": handoff.slot,
                "executor_id": handoff.executor_id,
                "creation_digest": sha256_hex(body.creation),
            },
        )
        try:
            evidence = await self._log.append(event, authorization=authorization)
        except LogError as exc:
            # No operation or privileged creation has started; retry the request.
            return {
                "operation_id": correlation,
                "correlation_id": correlation,
                "status": "audit_unavailable",
                "retryable": exc.retryable,
            }
        # Audit may have awaited I/O: recheck both approvals and live permissions
        # before handing execution to the independently bound Genesis client.
        await self._authority.resolve_agent_principal(key)
        await self._authority.check_permit(permit)
        fresh = await self._authority.resolve_agent_principal(executor.permit.key)
        if fresh.permit != executor.permit:
            raise EventPermissionError("Genesis execution authority changed")
        execution_client = self._exchange.bind(fresh)
        try:
            result = await self._provisioner.provision(execution_client, request)
            response = result.model_dump(mode="json")
        except GenesisOperationInProgress as exc:
            response = {
                "operation_id": str(exc.operation_id),
                "correlation_id": str(exc.operation_id),
                "status": "in_progress",
                "interrupted": exc.interrupted,
                "retryable": True,
            }
        except GenesisAuditError as exc:
            response = {
                "operation_id": str(exc.operation_id),
                "correlation_id": str(exc.operation_id),
                "status": "audit_unavailable",
                "retryable": exc.retryable,
            }
        finally:
            execution_client.revoke()
            await execution_client.drain()
        # Never release a result to a revoked initiator, including direct adapter calls.
        await self._authority.resolve_agent_principal(key)
        await self._authority.resolve_agent_principal(executor.permit.key)
        return {**response, "initiation_event_id": evidence.record.event_id}


def register_genesis_initiation(
    registry: SynapticRegistry, capability: GenesisInitiation
) -> SynapticBinding:
    return registry.register(
        SIGNAL,
        version=VERSION,
        description="Initiate governed creation of the policy-pinned Enki identity",
        metadata={
            "access": "system",
            "deterministic": True,
            "synaptic": SynapticToolSpec(
                capabilities=[SynapticCapabilitySpec(signal=SIGNAL, access="system")]
            ).model_dump(),
        },
        component=SynapticBinding(SIGNAL, PROVIDER, capability.handle, VERSION),
    )
