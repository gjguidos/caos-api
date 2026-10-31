"""REV-4: governed application of approved Agent revisions.

Real (disposable) MongoDB for the Agent, revisions, drafts, reviews, approvals, claims,
OPS V2 managed operations and Seshat; real UTU/CPBX scoped authority with the exact
``store.revise.approved`` binding. Nothing is activated and no grant changes.

Requires ``OPS2_MONGO_INTEGRATION=1``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
import pytest_asyncio

from src.base.events.v2.contracts import EventPermissionError
from src.base.operations.v2.mongo_managed_repository import MongoManagedOperationRepository
from src.domains.agentverse.agents.definition_revisions.application import (
    APPLICATIONS_COLLECTION,
    APPLIED,
    REVISE_BINDING,
    REVISE_SCOPE,
    REVISE_SIGNAL,
    SELECTED,
    AgentProjection,
    MongoRevisionApplicationRepository,
    RevisionApplicationConflict,
    RevisionApplicationInProgress,
    RevisionApplicationService,
    RevisionApplicationUnavailable,
    RevisionStoreCapability,
    claim_id,
    register_store_revise_approved,
)
from src.domains.agentverse.agents.definition_revisions.approval import (
    MongoRevisionApprovalRepository,
    RevisionApprovalConsumed,
    RevisionApprovalExpired,
    RevisionApprovalNotEffective,
    RevisionApprovalService,
    RevisionApprovalWithdrawn,
    RevisionWithdrawalRequest,
)
from src.domains.agentverse.agents.definition_revisions.canonical import (
    canonical_definition,
    definition_digest,
)
from src.domains.agentverse.agents.definition_revisions.gateway import (
    REVISION_VALIDATION_SCOPE,
)
from src.domains.agentverse.agents.definition_revisions.review import (
    MongoRevisionReviewRepository,
)
from src.domains.agentverse.agents.definition_revisions.tests.test_revision_approval import (
    approve_request,
)
from src.domains.agentverse.agents.definition_revisions.tests.test_revision_review import (
    create,
    rev,  # noqa: F401 - fixture re-export
)
from src.domains.agentverse.audit.models import LOGS_COLLECTION
from src.domains.agentverse.audit.repository import LogUnavailable, MongoLogRepository
from src.domains.agentverse.audit.service import CAOSLogService
from src.domains.agentverse.ncsc.authorization import SynapticAuthorization
from src.domains.agentverse.ncsc.models import SynapticGrants
from src.domains.agentverse.ncsc.tests.test_scoped_authority import KEY, STORE, VALIDATE
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey
from src.domains.agentverse.security.revocation import policy_version

WITHDRAW = RevisionWithdrawalRequest(
    contract="caos.agent_definition_revision_withdrawal_request", schema_version=1, confirmed=True
)
GRANTS = frozenset({VALIDATE.signal, STORE.signal, REVISE_SIGNAL})


def with_revise_grant(env) -> None:
    """The approved contract: Genesis (the executor) holds the exact revise signal."""
    policy = SynapticAuthorization(grants={(KEY.tenant_id, KEY.agent_id): SynapticGrants(GRANTS)})
    env.authority.reload_policy(policy)
    previous = env.revocations.read.return_value
    env.revocations.read.return_value = (policy_version(policy), *previous[1:])
    env.policy = policy


@pytest_asyncio.fixture
async def app_env(rev):  # noqa: F811
    r = rev
    with_revise_grant(r.env)
    audit = CAOSLogService(repository=MongoLogRepository(r.mongo))
    approvals = RevisionApprovalService(
        revisions=r.service,
        repository=MongoRevisionApprovalRepository(r.mongo),
        audit=audit,
        clock=r.clock,
    )
    applications = MongoRevisionApplicationRepository(r.mongo)
    projection = AgentProjection(r.mongo)
    capability = RevisionStoreCapability(
        authority=r.env.authority,
        applications=applications,
        approvals=approvals,
        reviews=MongoRevisionReviewRepository(r.mongo),
        revisions=r.revisions,
        projection=projection,
        clock=r.clock,
    )
    register_store_revise_approved(r.env.registry, capability)
    operations = MongoManagedOperationRepository(r.mongo, "operations_revision_test")

    def service(**overrides):
        return RevisionApplicationService(
            revisions=r.service,
            approvals=approvals,
            applications=applications,
            operations=operations,
            authority=r.env.authority,
            exchange=r.env.exchange,
            audit=audit,
            **({"clock": r.clock} | overrides),
        )

    async def approved(key="draft-key-0000000001", **changes):
        draft, _ = await r.service.create_draft(r.session, r.agent_id, create(**changes), key)
        review = await r.service.review(r.session, r.agent_id, draft.draft_id, 1)
        assert review.eligible, review.envelope["eligibility"]
        return await approvals.approve(r.session, r.agent_id, approve_request(review))

    return SimpleNamespace(
        r=r,
        audit=audit,
        approvals=approvals,
        applications=applications,
        projection=projection,
        capability=capability,
        operations=operations,
        service=service(),
        make=service,
        approved=approved,
    )


async def agent(a) -> dict:
    (document,) = await a.projection.holders(a.r.agent_id)
    return document


async def events(a, event_type):
    logs = await a.r.mongo.get_collection(LOGS_COLLECTION)
    return await logs.find({"event_type": event_type}, {"_id": 0}).to_list(None)


# --- Application ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_effective_approval_is_applied_exactly_once(app_env):
    a, r = app_env, app_env.r
    before = await agent(a)
    approval = await a.approved(prompt="You are Nabu, revised.")
    record = await a.service.apply(r.session, r.agent_id, approval.approval_id)
    assert record.state == "completed" and record.target_revision == 2
    assert record.public()["runtime"] == "reconciliation_pending"
    after = await agent(a)
    # The projection is exactly revision 2; identity, creation evidence and runtime fields
    # are preserved.
    chain = await r.revisions.chain(KEY.tenant_id, r.agent_id)
    assert [x.revision for x in chain] == [1, 2] and chain[0].origin == "baseline"
    assert after["definition_revision"]["revision_digest"] == chain[1].revision_digest
    assert (
        definition_digest(KEY.tenant_id, r.agent_id, canonical_definition(after))
        == chain[1].definition_digest
    )
    assert after["prompt"] == "You are Nabu, revised."
    assert after["tools"] == [{"key": "conversation", "config": {"modes": ["context", "llm"]}}]
    for field in (
        "id",
        "tenant_id",
        "creator",
        "ownership",
        "exposure",
        "runtime_version",
        "store",
        "created_at",
        "llm_type",
        "dna_sequence",
    ):
        assert after[field] == before[field], field
    # Bounded evidence, one per fact.
    (selected,) = await events(a, SELECTED)
    (applied,) = await events(a, APPLIED)
    assert selected["metadata"]["revision_digest"] == chain[1].revision_digest
    assert "You are Nabu" not in json.dumps([selected, applied], default=str)
    # The claim is the point of no withdrawal; replay changes nothing.
    with pytest.raises(RevisionApprovalConsumed):
        await a.approvals.withdraw(r.session, r.agent_id, approval.approval_id, WITHDRAW)
    again = await a.service.apply(r.session, r.agent_id, approval.approval_id)
    assert again == record and (await agent(a)) == after
    assert len(await events(a, SELECTED)) == len(await events(a, APPLIED)) == 1
    # No grant changed; nothing was activated (no lifecycle or Khepri dependency exists).
    assert {s for g in r.env.policy._grants.values() for s in g.invoke} == set(GRANTS)
    assert not hasattr(a.service, "_khepri") and not hasattr(a.capability, "_khepri")


@pytest.mark.asyncio
async def test_withdrawn_expired_and_pending_approvals_are_not_applied(app_env):
    a, r = app_env, app_env.r
    withdrawn = await a.approved()
    await a.approvals.withdraw(r.session, r.agent_id, withdrawn.approval_id, WITHDRAW)
    with pytest.raises(RevisionApprovalWithdrawn):
        await a.service.apply(r.session, r.agent_id, withdrawn.approval_id)
    expiring = await a.approved(key="draft-key-0000000002", prompt="Expiring.")
    r.clock.now += timedelta(hours=25)
    with pytest.raises(RevisionApprovalExpired):
        await a.service.apply(r.session, r.agent_id, expiring.approval_id)
    r.clock.now -= timedelta(hours=25)
    pending = await a.approved(key="draft-key-0000000003", prompt="Pending.")
    approvals = await r.mongo.get_collection("coll_agent_definition_revision_approvals")
    await approvals.update_one(
        {"_id": pending.approval_id},
        {"$set": {"status": "pending", "approved_at": None, "approval_event_id": None}},
    )
    with pytest.raises(RevisionApprovalNotEffective):
        await a.service.apply(r.session, r.agent_id, pending.approval_id)
    claims = await r.mongo.get_collection(APPLICATIONS_COLLECTION)
    assert await claims.count_documents({"state": {"$ne": "failed"}}) == 0
    assert "definition_revision" not in await agent(a)


@pytest.mark.asyncio
async def test_approval_and_withdrawal_race_has_one_outcome(app_env):
    a, r = app_env, app_env.r
    approval = await a.approved()
    applied, withdrawn = await asyncio.gather(
        a.service.apply(r.session, r.agent_id, approval.approval_id),
        a.approvals.withdraw(r.session, r.agent_id, approval.approval_id, WITHDRAW),
        return_exceptions=True,
    )
    if isinstance(withdrawn, Exception):
        assert isinstance(withdrawn, RevisionApprovalConsumed) and applied.state == "completed"
        assert "definition_revision" in await agent(a)
    else:
        assert isinstance(applied, RevisionApprovalWithdrawn) and withdrawn.status == "withdrawn"
        assert "definition_revision" not in await agent(a)
        claim = await a.applications.get(
            KEY.tenant_id, claim_id(KEY.tenant_id, r.agent_id, approval.predecessor.revision_digest)
        )
        assert claim is None or (claim.state == "failed" and claim.selected_revision_digest is None)


@pytest.mark.asyncio
async def test_competing_candidates_for_one_predecessor_are_deterministic_conflicts(app_env):
    a, r = app_env, app_env.r
    first = await a.approved(prompt="First.")
    second = await a.approved(key="draft-key-0000000002", prompt="Second.")
    results = await asyncio.gather(
        a.service.apply(r.session, r.agent_id, first.approval_id),
        a.make().apply(r.session, r.agent_id, second.approval_id),
        return_exceptions=True,
    )
    winners = [x for x in results if not isinstance(x, Exception)]
    losers = [x for x in results if isinstance(x, Exception)]
    assert len(winners) == 1 and winners[0].state == "completed"
    assert len(losers) == 1 and isinstance(
        losers[0],
        RevisionApplicationConflict | RevisionApplicationInProgress | RevisionApprovalNotEffective,
    )
    assert [x.revision for x in await r.revisions.chain(KEY.tenant_id, r.agent_id)] == [1, 2]
    loser = second if winners[0].approval_id == first.approval_id else first
    with pytest.raises(RevisionApplicationConflict) as conflict:
        await a.service.apply(r.session, r.agent_id, loser.approval_id)
    assert conflict.value.code == "revision_application.predecessor_claimed"


# --- Crash recovery -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_crash_after_claim_resumes_the_same_application(app_env):
    a, r = app_env, app_env.r
    approval = await a.approved()
    service = a.make()
    service._execute = AsyncMock(side_effect=RuntimeError("process died"))
    with pytest.raises(RuntimeError):
        await service.apply(r.session, r.agent_id, approval.approval_id)
    with pytest.raises(RevisionApprovalConsumed):  # Claimed: withdrawal no longer cancels.
        await a.approvals.withdraw(r.session, r.agent_id, approval.approval_id, WITHDRAW)
    record = await a.service.apply(r.session, r.agent_id, approval.approval_id)
    assert record.state == "completed"


@pytest.mark.asyncio
async def test_crash_after_selection_applies_the_same_selected_revision(app_env):
    a, r = app_env, app_env.r
    approval = await a.approved()
    original = a.projection.compare_and_set
    a.projection.compare_and_set = AsyncMock(side_effect=RuntimeError("crash before CAS"))
    with pytest.raises(RuntimeError):
        await a.service.apply(r.session, r.agent_id, approval.approval_id)
    selected = await r.revisions.get(KEY.tenant_id, r.agent_id, 2)
    assert selected is not None and "definition_revision" not in await agent(a)
    a.projection.compare_and_set = original
    record = await a.service.apply(r.session, r.agent_id, approval.approval_id)
    assert record.selected_revision_digest == selected.revision_digest
    assert (await agent(a))["definition_revision"]["revision_digest"] == selected.revision_digest


@pytest.mark.asyncio
async def test_crash_after_cas_reconciles_without_applying_twice(app_env):
    a, r = app_env, app_env.r
    approval = await a.approved()
    service = a.make()
    original = service._receipts
    service._receipts = AsyncMock(side_effect=RuntimeError("crash after CAS"))
    with pytest.raises(RuntimeError):
        await service.apply(r.session, r.agent_id, approval.approval_id)
    first = await agent(a)
    assert first["definition_revision"]["revision"] == 2
    service._receipts = original
    record = await service.apply(r.session, r.agent_id, approval.approval_id)
    assert record.state == "completed"
    assert (await agent(a))["definition_revision"] == first["definition_revision"]  # not reapplied


@pytest.mark.asyncio
async def test_missing_seshat_evidence_is_discoverable_and_reconciled(app_env):
    a, r = app_env, app_env.r
    approval = await a.approved()
    append = a.audit.append
    a.audit.append = AsyncMock(side_effect=LogUnavailable("down"))
    with pytest.raises(RevisionApplicationUnavailable):
        await a.service.apply(r.session, r.agent_id, approval.approval_id)
    (missing,) = await a.applications.missing_evidence(KEY.tenant_id)
    assert missing.state == "selected" and missing.selection_event_id is None
    a.audit.append = append
    record = await a.service.apply(r.session, r.agent_id, approval.approval_id)
    assert (
        record.state == "completed" and await a.applications.missing_evidence(KEY.tenant_id) == []
    )
    assert len(await events(a, SELECTED)) == len(await events(a, APPLIED)) == 1


@pytest.mark.asyncio
async def test_a_live_lease_is_fenced_and_an_expired_one_is_reconciled_not_failed(app_env):
    a, r = app_env, app_env.r
    approval = await a.approved()

    def now() -> datetime:  # Leases are evaluated against server time.
        return datetime.now(UTC)

    crashed = a.make(clock=now, lease_ms=300)

    async def die_holding_the_lease(record, revise):
        managed = await crashed._open(UUID(record.application_id))
        await crashed._activate(managed)
        raise RuntimeError("worker died holding its lease")

    crashed._execute = die_holding_the_lease
    with pytest.raises(RuntimeError):
        await crashed.apply(r.session, r.agent_id, approval.approval_id)
    with pytest.raises(RevisionApplicationInProgress):
        await a.make(clock=now).apply(r.session, r.agent_id, approval.approval_id)
    await asyncio.sleep(0.5)
    record = await a.make(clock=now).apply(r.session, r.agent_id, approval.approval_id)
    assert record.state == "completed"
    managed = await a.operations.inspect(UUID(record.application_id))
    assert managed.snapshot.state.value == "succeeded"
    # The expired attempt was reconciled (never treated as a failed application).
    assert [x.state.value for x in managed.snapshot.attempts] == ["failed", "succeeded"]
    assert "reconciled" in (managed.snapshot.attempts[0].reason or "")


# --- Identity and authority -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_duplicate_identities_and_changed_predecessors_fail_closed(app_env):
    a, r = app_env, app_env.r
    approval = await a.approved()
    (row,) = await r.agents.find({"id": r.agent_id}, {"_id": 0}).to_list(None)
    twin = {k: v for k, v in row.items() if k != "store"}
    await r.agents.insert_one({**twin, "tenant_id": "other-tenant"})
    with pytest.raises(RevisionApprovalNotEffective):  # baseline no longer verifiable (DEF-6)
        await a.service.apply(r.session, r.agent_id, approval.approval_id)
    await r.agents.delete_one({"tenant_id": "other-tenant"})
    await r.agents.update_one(
        {"id": r.agent_id},
        {"$set": {"prompt": "Changed.", "updated_at": datetime(2026, 11, 1, tzinfo=UTC)}},
    )
    with pytest.raises(RevisionApprovalNotEffective) as stale:
        await a.service.apply(r.session, r.agent_id, approval.approval_id)
    assert stale.value.code == "revision_approval.stale_predecessor"
    assert await r.revisions.chain(KEY.tenant_id, r.agent_id) == []


@pytest.mark.asyncio
async def test_wrong_tenant_owner_and_bootstrap_are_denied(app_env):
    a, r = app_env, app_env.r
    approval = await a.approved()
    for session in (
        dataclasses.replace(r.session, user_id="intruder"),
        dataclasses.replace(r.session, origin="bootstrap"),
    ):
        with pytest.raises(EventPermissionError):
            await a.service.apply(session, r.agent_id, approval.approval_id)
    await r.agents.update_one({"id": r.agent_id}, {"$set": {"creator": "someone-else"}})
    with pytest.raises(EventPermissionError):
        await a.service.apply(r.session, r.agent_id, approval.approval_id)


@pytest.mark.asyncio
async def test_the_signal_requires_the_exact_scope_executor_and_claim(app_env):
    a, r = app_env, app_env.r
    approval = await a.approved()
    payload = {"resource_type": "agent", "claim_id": "x", "application_id": "y"}
    key = AgentInstanceKey(KEY.tenant_id, KEY.user_id, KEY.agent_id, KEY.session_id)
    principal = await r.env.authority.resolve_agent_principal(key)
    # Unscoped (the generic signal route's permit): denied before any read or write.
    with pytest.raises(EventPermissionError):
        await r.env.exchange.bind(principal).request(REVISE_SIGNAL, payload, version="1.0.0")
    # Another scope (revision validation) cannot reach the revise signal.
    validation = await r.env.authority.attenuate(principal, REVISION_VALIDATION_SCOPE)
    with pytest.raises(EventPermissionError):
        await r.env.exchange.bind(validation).request(REVISE_SIGNAL, payload, version="1.0.0")
    revise = await r.env.authority.attenuate(principal, REVISE_SCOPE)
    # Correctly scoped but no claim: a deterministic failure, never a write.
    result = await r.env.exchange.bind(revise).request(REVISE_SIGNAL, payload, version="1.0.0")
    assert (result["success"], result["meta"]["code"]) == (
        False,
        "revision_application.claim_mismatch",
    )
    # A principal without the root grant cannot even be attenuated to the scope.
    ungranted = SynapticAuthorization(
        grants={(KEY.tenant_id, KEY.agent_id): SynapticGrants(frozenset({VALIDATE.signal}))}
    )
    r.env.authority.reload_policy(ungranted)
    r.env.revocations.read.return_value = (
        policy_version(ungranted),
        *r.env.revocations.read.return_value[1:],
    )
    with pytest.raises(EventPermissionError):
        await r.env.authority.attenuate(
            await r.env.authority.resolve_agent_principal(key), REVISE_SCOPE
        )
    assert (
        REVISE_BINDING.provider == "system.store"
        and REVISE_SCOPE.operation == "agent.definition.revise"
    )
    assert "definition_revision" not in await agent(a) and approval.consumed_by is None


def test_local_policy_declares_no_revise_grant_and_accepts_only_genesis():
    from src.domains.agentverse.dependencies import genesis_local

    with open("config/genesis.local.json") as file:
        policy = json.load(file)
    assert not [s for g in policy["grants"] for s in g["invoke"] if s == REVISE_SIGNAL]
    assert REVISE_SIGNAL in genesis_local._GENESIS_REVISION_GRANTS
    for name in ("_ENKI_REASONING_GRANTS", "_CHAT_GRANTS"):
        value = getattr(genesis_local, name)
        sets = value if isinstance(value, tuple) else (value,)
        assert not [s for g in sets for s in g if s == REVISE_SIGNAL]
    assert not policy["trusted_lines"]
