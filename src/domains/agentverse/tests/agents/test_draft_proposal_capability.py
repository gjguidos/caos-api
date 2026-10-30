"""``agent.draft.propose@1.0.0`` provider guards (DRAFT-0.2A closeout).

Real permits, CPBX client and proposal service; UTU authority is a stub so each
failure mode (revoked, expired, changed or missing authority) can be forced.
End-to-end through the real local policy: test_draft_proposal_capability_integration.py.
"""

from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.agents.creation_drafts.capability import (
    PROPOSE_BINDING,
    PROPOSE_PROVIDER,
    PROPOSE_SCOPE,
    PROPOSE_VERSION,
    DraftProposalCapability,
    register_draft_propose,
)
from src.domains.agentverse.agents.creation_drafts.proposals import PROPOSALS_COLLECTION
from src.domains.agentverse.ncsc.cpbx import AuthorizedSynapticClient
from src.domains.agentverse.ncsc.models import Synapse, SynapticExecutionContext
from src.domains.agentverse.ncsc.tests.test_scoped_authority import KEY
from src.domains.agentverse.registries.synaptic_registry import SynapticRegistry
from src.domains.agentverse.security.capability_scope import CapabilityBinding, CapabilityScope
from src.domains.agentverse.security.revocation import ExecutionPermit
from src.domains.agentverse.tests.agents.test_creation_drafts import (  # noqa: F401
    created,
    drafts,
)
from src.domains.agentverse.tests.agents.test_draft_proposals import (  # noqa: F401
    proposals,
)
from src.domains.agentverse.tests.agents.test_validation_gateway import stack  # noqa: F401

SOURCE_KEY = replace(KEY, agent_id="source")


def permit(**overrides) -> ExecutionPermit:
    fields = {
        "key": SOURCE_KEY,
        "versions": ("policy:7", "incarnation", "0", "0"),
        "origin": "session",
        "scope": PROPOSE_SCOPE,
        "binding_chain": (PROPOSE_BINDING,),
    }
    return ExecutionPermit(**{**fields, **overrides})


def call(issued: ExecutionPermit, payload, **synapse_overrides):
    client = AuthorizedSynapticClient(
        exchange=None,  # type: ignore[arg-type]
        context=SynapticExecutionContext(issued.key.agent_id, issued.key.tenant_id, permit=issued),
    )
    synapse = Synapse(
        **{
            "signal": "agent.draft.propose",
            "source": issued.key.agent_id,
            "target": PROPOSE_PROVIDER,
            "tenant_id": issued.key.tenant_id,
            "payload": payload,
            "provider_version": PROPOSE_VERSION,
            "entry_operation": PROPOSE_SCOPE.operation,
            **synapse_overrides,
        }
    )
    return synapse, client


def submission(draft, *, key="submission-key-000001", **proposal):
    return {
        "contract": "caos.agent_draft_proposal_submission",
        "schema_version": 1,
        "idempotency_key": key,
        "proposal": {
            "contract": "caos.agent_draft_proposal_request",
            "schema_version": 1,
            "draft_id": draft.draft_id,
            "base_revision": draft.revision,
            "changes": {"prompt": "Proposed by the source Agent"},
            **proposal,
        },
    }


@pytest.fixture
def capability(proposals):  # noqa: F811
    s = proposals
    issued = permit()
    s.authority = SimpleNamespace(
        check_permit=AsyncMock(),
        resolve_agent_principal=AsyncMock(
            return_value=SimpleNamespace(permit=SimpleNamespace(versions=issued.versions))
        ),
        grants_for=Mock(return_value=SimpleNamespace(invoke=frozenset({"agent.draft.propose"}))),
    )
    s.capability = DraftProposalCapability(authority=s.authority, proposals=s.proposals)  # type: ignore[arg-type]
    return s


@pytest.mark.asyncio
async def test_the_scoped_source_agent_submits_idempotently_and_learns_no_content(capability):
    s = capability
    draft = await created(s)
    receipt = await s.capability.propose(*call(permit(), submission(draft)))
    assert receipt["ok"] and receipt["created"] and receipt["state"] == "pending"
    assert set(receipt) == {
        "contract",
        "ok",
        "proposal_id",
        "draft_id",
        "base_revision",
        "state",
        "created",
    }
    again = await s.capability.propose(*call(permit(), submission(draft)))
    assert again == {**receipt, "created": False}
    other = await s.capability.propose(
        *call(permit(), submission(draft, changes={"prompt": "Different"}))
    )
    assert (other["ok"], other["code"]) == (False, "idempotency.conflict")
    stored = s.store.documents(PROPOSALS_COLLECTION)[receipt["proposal_id"]]
    assert (stored["owner_subject_id"], stored["proposer_agent_id"]) == (KEY.user_id, "source")
    assert stored["authorization"]["principal_id"] == "source"
    assert s.authority.check_permit.await_count >= 2  # Before work and before the receipt.
    s.authority.grants_for.assert_called()


OTHER = CapabilityBinding("data.validation", "system.validation", "1.0.0")


@pytest.mark.parametrize(
    "issued, overrides",
    [
        (permit(scope=None, binding_chain=()), {}),  # Unscoped: e.g. the generic signal route.
        (
            permit(
                scope=CapabilityScope(
                    "agent.draft.propose", PROPOSE_BINDING, frozenset({PROPOSE_BINDING, OTHER})
                ),
            ),
            {},
        ),
        (permit(origin="bootstrap"), {}),
        (permit(), {"provider_version": "1.0.1"}),
        (permit(), {"provider_version": None}),
        (permit(), {"entry_operation": "agent.creation_draft.validate"}),
        (permit(), {"target": "system.store"}),
        (permit(), {"source": "other-agent"}),
        (permit(), {"tenant_id": "other-tenant"}),
    ],
)
@pytest.mark.asyncio
async def test_unscoped_mismatched_or_bootstrap_callers_are_denied_before_any_work(
    capability, issued, overrides
):
    s = capability
    draft = await created(s)
    with pytest.raises(EventPermissionError):
        await s.capability.propose(*call(issued, submission(draft), **overrides))
    assert s.store.documents(PROPOSALS_COLLECTION) == {}


@pytest.mark.parametrize("failure", ["revoked", "changed", "ungranted"])
@pytest.mark.asyncio
async def test_revoked_expired_changed_or_ungranted_authority_is_denied(capability, failure):
    s = capability
    draft = await created(s)
    if failure == "revoked":  # Also session logout and permit expiry.
        s.authority.check_permit.side_effect = EventPermissionError("revoked")
    elif failure == "changed":
        s.authority.resolve_agent_principal.return_value = SimpleNamespace(
            permit=SimpleNamespace(versions=("policy:8", "incarnation", "0", "0"))
        )
    else:
        s.authority.grants_for.return_value = SimpleNamespace(invoke=frozenset({"chat.context"}))
    with pytest.raises(EventPermissionError):
        await s.capability.propose(*call(permit(), submission(draft)))
    assert s.store.documents(PROPOSALS_COLLECTION) == {}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: {**p, "owner_subject_id": "x"},
        lambda p: {**p, "tenant_id": "x"},
        lambda p: {**p, "approved": True},
        lambda p: {**p, "proposal": {**p["proposal"], "status": "accepted"}},
        lambda p: {**p, "proposal": {**p["proposal"], "proposer_agent_id": "x"}},
        lambda p: {**p, "proposal": {**p["proposal"], "changes": {"creator": "x"}}},
        lambda p: {**p, "idempotency_key": "short"},
        lambda p: {k: v for k, v in p.items() if k != "idempotency_key"},
    ],
)
@pytest.mark.asyncio
async def test_arbitrary_fields_and_privileged_metadata_are_refused(capability, mutate):
    s = capability
    draft = await created(s)
    receipt = await s.capability.propose(*call(permit(), mutate(submission(draft))))
    assert (receipt["ok"], receipt["code"]) == (False, "proposal.invalid_request")
    assert s.store.documents(PROPOSALS_COLLECTION) == {}


@pytest.mark.asyncio
async def test_only_the_drafts_source_agent_for_its_own_human_may_propose(capability):
    s = capability
    draft = await created(s)
    for key in (
        replace(SOURCE_KEY, agent_id="other-agent"),
        replace(SOURCE_KEY, user_id="other-human"),
    ):
        receipt = await s.capability.propose(*call(permit(key=key), submission(draft)))
        assert (receipt["ok"], receipt["code"]) == (False, "draft.not_found")
    assert s.store.documents(PROPOSALS_COLLECTION) == {}


def test_registration_binds_the_exact_signal_provider_and_version(capability):
    registry = SynapticRegistry()
    binding = register_draft_propose(registry, capability.capability)
    assert (binding.signal, binding.provider, binding.version) == (
        "agent.draft.propose",
        "system.agent_drafts",
        "1.0.0",
    )
