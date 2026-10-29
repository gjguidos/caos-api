"""Genesis authority handoffs, not an orchestrator or principal impersonation.

CPBX keeps the initiator's principal on entry (JOSHUA for Enki, G1.4; Enki for
Thoth, G2.1). Only this trusted adapter may resolve the separately approved Genesis
identity, for the same live session and user, using a server-pinned handoff. Each
handoff slot has its own exact signal, so a grant for one creation contract never
reaches another. No permit or client is returned to the initiating agent.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.base.events.v2.contracts import EventPermissionError
from src.base.operations.v2.admission import sha256_hex
from src.domains.agentverse.agents.genesis import GenesisProvisioner, genesis_operation_id
from src.domains.agentverse.agents.genesis.activation import GenesisActivation
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
THOTH_SIGNAL = "genesis.thoth.request"
# One exact initiation contract per pinned slot.
SIGNALS = {"enki": SIGNAL, "thoth": THOTH_SIGNAL}
# Executor-only capabilities an initiator must never hold itself.
_EXECUTOR_SIGNALS = frozenset({"data.validation", "store.create", "store.create.provisioned"})
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
    slot: Literal["enki", "thoth"]
    # Creation never implies activation; only a reviewed opt-in reaches Khepri, which
    # still resolves the new agent's own UTU authority in the requester's session.
    activation: Literal["none", "requester_session"] = "none"

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
        activation: GenesisActivation | None = None,
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
        if activation is None and any(h.activation != "none" for h in handoffs):
            raise ValueError("A Genesis handoff that activates requires Khepri activation")
        self._activation = activation

    @property
    def signals(self) -> tuple[str, ...]:
        return tuple(sorted({SIGNALS[h.slot] for h in self._handoffs.values()}))

    async def handle(self, synapse: Synapse, client: AuthorizedSynapticClient) -> dict[str, Any]:
        body = EnkiInitiationRequest.model_validate(synapse.payload)
        permit = client.execution_permit if isinstance(client, AuthorizedSynapticClient) else None
        if not isinstance(permit, ExecutionPermit) or (
            synapse.target,
            synapse.tenant_id,
            synapse.source,
        ) != (PROVIDER, permit.key.tenant_id, permit.key.agent_id):
            raise EventPermissionError("Genesis initiation requires its verified CPBX caller")
        key = permit.key
        handoff = self._handoffs.get((key.tenant_id, key.agent_id, key.user_id))
        if handoff is None or synapse.signal != SIGNALS[handoff.slot]:
            raise EventPermissionError("Genesis handoff denied")
        signal = synapse.signal
        # Re-read current approvals, definitions, membership and authority, even
        # for terminal replay. A once-issued permit is not a current approval.
        initiator = await self._authority.resolve_agent_principal(key)
        if initiator.permit != permit:
            raise EventPermissionError("Genesis initiating permit changed")
        self._authority.require_invoke(
            context=SynapticExecutionContext(
                principal_id=key.agent_id, tenant_id=key.tenant_id, permit=permit
            ),
            signal=signal,
        )
        executor = await self._authority.resolve_agent_principal(
            AgentInstanceKey(key.tenant_id, key.user_id, handoff.executor_id, key.session_id)
        )
        # The initiator holds its own contract and never an executor capability or
        # another slot's contract; the executor holds exactly its two capabilities.
        initiator_grants = self._authority.grants_for(initiator).invoke
        if (
            signal not in initiator_grants
            or initiator_grants & (_EXECUTOR_SIGNALS | (set(SIGNALS.values()) - {signal}))
            or self._authority.grants_for(executor).invoke
            != frozenset({"data.validation", "store.create.provisioned"})
        ):
            raise EventPermissionError("Genesis handoff grants differ from the reviewed policy")
        pinned = self._provisions[handoff.slot]
        if (body.creation.get("name"), body.creation.get("type")) != (pinned.name, pinned.type):
            # Guard BEFORE opening/replaying an operation: a historical chat
            # success is not approval to recreate the now form-pinned identity.
            raise EventPermissionError("Genesis request differs from the current pinned definition")
        profile = (body.creation.get("role_name"), body.creation.get("personality_key"))
        if pinned.type == "form" and None in (pinned.role_name, pinned.personality_key):
            # A conversational Form is only creatable with a fully reviewed composition.
            raise EventPermissionError("Genesis form slots require a pinned composition")
        if any(
            pin is not None and value != pin
            for pin, value in zip((pinned.role_name, pinned.personality_key), profile, strict=True)
        ):
            raise EventPermissionError("Genesis request differs from the pinned composition")
        request = GenesisProvisionRequest(slot=handoff.slot, creation=body.creation)
        operation_id = genesis_operation_id(executor.permit, request)
        correlation = str(operation_id)
        authorization = permit_provenance(permit, signal)
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
            target={"signal": signal, "version": VERSION},
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
        if handoff.activation == "requester_session" and response.get("status") == "provisioned":
            # A separate authority: Khepri resolves the new agent's own UTU permit.
            assert self._activation is not None
            stored = next(
                (
                    r
                    for r in await self._log.history(key.tenant_id, response["correlation_id"])
                    if r.event_type == "genesis.store.completed"
                ),
                None,
            )
            if stored is None:
                raise EventPermissionError("Genesis activation requires store evidence")
            response["activation"] = await self._activation.activate(
                requester=permit,
                handoff=handoff,
                correlation_id=response["correlation_id"],
                causation_id=stored.event_id,
            )
        # Never release a result to a revoked initiator, including direct adapter calls.
        await self._authority.resolve_agent_principal(key)
        await self._authority.resolve_agent_principal(executor.permit.key)
        return {**response, "initiation_event_id": evidence.record.event_id}


def register_genesis_initiation(
    registry: SynapticRegistry, capability: GenesisInitiation
) -> SynapticBinding:
    """Register one exact initiation contract per configured handoff slot.

    Registration grants nothing; each initiator still needs its own CPBX grant.
    Returns the first binding (the Enki contract when configured).
    """
    bindings = [
        registry.register(
            signal,
            version=VERSION,
            description="Initiate governed creation of a policy-pinned Agent identity",
            metadata={
                "access": "system",
                "deterministic": True,
                "synaptic": SynapticToolSpec(
                    capabilities=[SynapticCapabilitySpec(signal=signal, access="system")]
                ).model_dump(),
            },
            component=SynapticBinding(signal, PROVIDER, capability.handle, VERSION),
        )
        for signal in capability.signals
    ]
    return next((b for b in bindings if b.signal == SIGNAL), bindings[0])
