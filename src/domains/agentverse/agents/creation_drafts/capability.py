"""``agent.draft.propose@1.0.0``: the source Agent's proposal capability (DRAFT-0.2A).

Approved for Enki only (reviewed local UTU policy). Executable only under its own
attenuated scope, so an unscoped Agent client (including the generic signal route)
cannot reach it. Tenant, requester (the draft owner), source Agent and session come
only from the CPBX-issued permit, re-verified against current UTU authority. The
capability submits a proposal and nothing else: it cannot edit drafts, decide
proposals, approve, create, activate or reach Store.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError

from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.agents.creation_drafts.models import DraftError
from src.domains.agentverse.agents.creation_drafts.proposal_service import (
    AgentDraftProposalService,
)
from src.domains.agentverse.agents.creation_drafts.proposals import (
    PROPOSE_SIGNAL,
    AgentDraftProposalRequest,
    IdempotencyKey,
    ProposalAuthor,
)
from src.domains.agentverse.ncsc.cpbx import AuthorizedSynapticClient, SynapticExchange
from src.domains.agentverse.ncsc.models import Synapse
from src.domains.agentverse.registries.synaptic_registry import SynapticBinding, SynapticRegistry
from src.domains.agentverse.security.capability_scope import CapabilityBinding, CapabilityScope
from src.domains.agentverse.security.revocation import ExecutionPermit
from src.domains.agentverse.security.utu import AuthorizedAgentPrincipal, UTUAuthority
from src.domains.agentverse.tools.spec import SynapticCapabilitySpec, SynapticToolSpec

PROPOSE_PROVIDER = "system.agent_drafts"
PROPOSE_VERSION = "1.0.0"
PROPOSE_BINDING = CapabilityBinding(PROPOSE_SIGNAL, PROPOSE_PROVIDER, PROPOSE_VERSION)
PROPOSE_SCOPE = CapabilityScope(PROPOSE_SIGNAL, PROPOSE_BINDING, frozenset({PROPOSE_BINDING}))
RECEIPT_CONTRACT = "caos.agent_draft_proposal_receipt"


class ProposalSubmission(BaseModel):
    """The whole synaptic payload: one typed proposal and its idempotency key. Identity,
    ownership, status and decision fields are not expressible."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    contract: Literal["caos.agent_draft_proposal_submission"]
    schema_version: StrictInt = Field(ge=1, le=1)
    idempotency_key: IdempotencyKey
    proposal: AgentDraftProposalRequest


class DraftProposalCapability:
    def __init__(self, *, authority: UTUAuthority, proposals: AgentDraftProposalService) -> None:
        self._authority = authority
        self._proposals = proposals

    async def propose(self, synapse: Synapse, client: AuthorizedSynapticClient) -> dict[str, Any]:
        permit = client.execution_permit if isinstance(client, AuthorizedSynapticClient) else None
        if (
            not isinstance(permit, ExecutionPermit)
            or (synapse.target, synapse.tenant_id, synapse.source)
            != (PROPOSE_PROVIDER, permit.key.tenant_id, permit.key.agent_id)
            or permit.scope != PROPOSE_SCOPE
            or not permit.binding_chain
            or permit.binding_chain[-1] != PROPOSE_BINDING
            or synapse.entry_operation != PROPOSE_SCOPE.operation
            or synapse.provider_version != PROPOSE_VERSION
            or permit.origin != "session"
        ):
            raise EventPermissionError("agent.draft.propose requires its verified scoped caller")
        # Current, not once-issued, authority: live session, enabled approval, grant.
        await self._authority.check_permit(permit)
        current = await self._authority.resolve_agent_principal(permit.key)
        # CPBX enforced the scoped path at dispatch; re-check the current root grant.
        if (
            current.permit.versions != permit.versions
            or PROPOSE_SIGNAL not in self._authority.grants_for(current).invoke
        ):
            raise EventPermissionError("Proposer authority changed")
        try:
            submission = ProposalSubmission.model_validate(synapse.payload)
        except ValidationError:
            return {
                "contract": RECEIPT_CONTRACT,
                "ok": False,
                "code": "proposal.invalid_request",
                "retryable": False,
            }
        try:
            proposal, created = await self._proposals.propose(
                ProposalAuthor.from_permit(permit),
                submission.proposal,
                submission.idempotency_key,
            )
        except DraftError as exc:
            return {
                "contract": RECEIPT_CONTRACT,
                "ok": False,
                "code": exc.code,
                "retryable": exc.retryable,
            }
        # Never release a receipt to a caller whose authority ended meanwhile.
        await self._authority.check_permit(permit)
        return {
            "contract": RECEIPT_CONTRACT,
            "ok": True,
            "proposal_id": proposal.proposal_id,
            "draft_id": proposal.draft_id,
            "base_revision": proposal.base_revision,
            "state": proposal.state(self._proposals.now()),
            "created": created,
        }


def register_draft_propose(
    registry: SynapticRegistry, capability: DraftProposalCapability
) -> SynapticBinding:
    """Trusted bootstrap registration. Registration grants no invocation permission."""
    return registry.register(
        PROPOSE_SIGNAL,
        description="Submit a typed proposal to the caller's own human's creation draft",
        version=PROPOSE_VERSION,
        metadata={
            "access": "system",
            "deterministic": True,
            "synaptic": SynapticToolSpec(
                capabilities=[SynapticCapabilitySpec(signal=PROPOSE_SIGNAL, access="system")]
            ).model_dump(),
        },
        component=SynapticBinding(
            PROPOSE_SIGNAL, PROPOSE_PROVIDER, capability.propose, PROPOSE_VERSION
        ),
    )


async def submit_proposal(
    authority: UTUAuthority,
    exchange: SynapticExchange,
    principal: AuthorizedAgentPrincipal,
    submission: ProposalSubmission,
) -> dict[str, Any]:
    """Trusted in-process caller for an Agent runtime (Enki's future turn plan).

    Attenuates the Agent's verified principal to the proposal scope before binding a
    client, so the call carries exactly this one capability. Never an HTTP factory.
    """
    scoped = await authority.attenuate(principal, PROPOSE_SCOPE)
    client = exchange.bind(scoped)  # NEVER bind the unattenuated principal.
    try:
        result = await client.request(
            PROPOSE_SIGNAL,
            submission.model_dump(mode="json"),
            target=PROPOSE_PROVIDER,
            version=PROPOSE_VERSION,
        )
    finally:
        client.revoke()
        await client.drain()
    return result["result"] if isinstance(result, dict) and "result" in result else result
