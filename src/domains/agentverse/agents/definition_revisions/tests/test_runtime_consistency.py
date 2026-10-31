"""REV-5: runtime definition consistency and reconciliation.

Disposable MongoDB and Redis; real UTU/CPBX, the V2 dispatcher, CONV-REC-0.1 journal and
the REV-4 application stack. Covers the REV-4 claim/consumption and projection invariants,
turn-bound definitions, replay semantics, history inspection, activity consent and the
stateless runtime instance fact.

Requires ``OPS2_MONGO_INTEGRATION=1``.
"""

from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.domains.agentverse.activities.models import ActivityContractUnavailable
from src.domains.agentverse.activities.owners import ConversationOwner
from src.domains.agentverse.activities.tests.test_activity_generic import (
    Attempt,
    env,  # noqa: F401 - fixture re-export
    start,
)
from src.domains.agentverse.agents.conversational_composition_v2 import (
    ConversationGate,
    bind_definition,
)
from src.domains.agentverse.agents.definition_revisions.application import (
    APPLICATIONS_COLLECTION,
    RevisionApplicationConflict,
)
from src.domains.agentverse.agents.definition_revisions.approval import (
    RevisionApprovalExpired,
)
from src.domains.agentverse.agents.definition_revisions.inspection import inspect_definition
from src.domains.agentverse.agents.definition_revisions.tests.test_revision_application import (
    app_env,  # noqa: F401 - fixture re-export
)
from src.domains.agentverse.agents.definition_revisions.tests.test_revision_review import (
    rev,  # noqa: F401 - fixture re-export
)
from src.domains.agentverse.agents.runtime_v2 import ManagedAgentV2
from src.domains.agentverse.nataraja.observe import MotorReadOnlyMongo, _definition_baselines
from src.domains.agentverse.ncsc.authorization import SynapticAuthorization
from src.domains.agentverse.ncsc.models import SynapticGrants
from src.domains.agentverse.ncsc.tests.test_scoped_authority import KEY
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey
from src.domains.agentverse.tests.communication.test_cognitive_response_v2 import (
    communication,  # noqa: F401 - fixture re-export
    response_env,  # noqa: F401 - fixture re-export
    session_env,  # noqa: F401 - fixture re-export
    store,  # noqa: F401 - fixture re-export
)
from src.domains.agentverse.tests.communication.test_turn_recovery_v2 import LEASE_MS, short

# --- Phase 1: REV-4 invariants --------------------------------------------------------------


async def agent(a) -> dict:
    (document,) = await a.projection.holders(a.r.agent_id)
    return document


@pytest.mark.asyncio
async def test_an_approval_expiring_between_check_and_consumption_is_never_consumed(app_env):  # noqa: F811
    a, r = app_env, app_env.r
    approval = await a.approved()
    claim = a.applications.claim

    async def claim_then_expire(record):
        stored = await claim(record)
        r.clock.now += timedelta(hours=25)  # expires before the consumption commit
        return stored

    a.applications.claim = claim_then_expire
    with pytest.raises(RevisionApprovalExpired):
        await a.service.apply(r.session, r.agent_id, approval.approval_id)
    approvals = await r.mongo.get_collection("coll_agent_definition_revision_approvals")
    (stored,) = await approvals.find({}, {"_id": 0}).to_list(None)
    assert stored["consumed_by"] is None and stored["consumed_at"] is None
    claims = await r.mongo.get_collection(APPLICATIONS_COLLECTION)
    (released,) = await claims.find({}, {"_id": 0}).to_list(None)
    assert released["state"] == "failed" and released["selected_revision_digest"] is None


@pytest.mark.asyncio
async def test_consumption_binds_the_exact_application_and_survives_a_lost_response(app_env):  # noqa: F811
    a, r = app_env, app_env.r
    approval = await a.approved()
    service = a.make()
    service._execute = AsyncMock(side_effect=RuntimeError("response lost after consumption"))
    with pytest.raises(RuntimeError):
        await service.apply(r.session, r.agent_id, approval.approval_id)
    approvals = await r.mongo.get_collection("coll_agent_definition_revision_approvals")
    (stored,) = await approvals.find({}, {"_id": 0}).to_list(None)
    claims = await r.mongo.get_collection(APPLICATIONS_COLLECTION)
    (claim,) = await claims.find({}, {"_id": 0}).to_list(None)
    assert stored["consumed_by"] == claim["application_id"]  # not the predecessor claim
    record = await a.service.apply(r.session, r.agent_id, approval.approval_id)
    assert record.state == "completed"


@pytest.mark.asyncio
async def test_a_released_claim_cannot_be_driven_by_the_spent_approval(app_env):  # noqa: F811
    a, r = app_env, app_env.r
    first = await a.approved(prompt="First.")
    second = await a.approved(key="draft-key-0000000002", prompt="Second.")
    service = a.make()
    service._execute = AsyncMock(side_effect=RuntimeError("first worker died"))
    with pytest.raises(RuntimeError):
        await service.apply(r.session, r.agent_id, first.approval_id)
    claims = await r.mongo.get_collection(APPLICATIONS_COLLECTION)
    # The first application definitively failed before selecting anything.
    await claims.update_one({}, {"$set": {"state": "failed", "issue_codes": ["test.failed"]}})
    record = await a.service.apply(r.session, r.agent_id, second.approval_id)
    assert record.state == "completed"
    with pytest.raises(RevisionApplicationConflict):
        await a.service.apply(r.session, r.agent_id, first.approval_id)
    assert [x.revision for x in await r.revisions.chain(KEY.tenant_id, r.agent_id)] == [1, 2]


@pytest.mark.asyncio
async def test_a_direct_mutation_before_the_cas_is_never_overwritten(app_env):  # noqa: F811
    a, r = app_env, app_env.r
    approval = await a.approved(prompt="Approved.")
    original = a.projection.compare_and_set

    async def mutate_first(document, expected, values):
        # A raw write that does not bump updated_at (no system writer does this).
        await r.agents.update_one({"id": r.agent_id}, {"$set": {"prompt": "Direct."}})
        return await original(document, expected, values)

    a.projection.compare_and_set = mutate_first
    # The fenced compare-and-set misses: a retryable outcome, nothing written.
    pending = await a.service.apply(r.session, r.agent_id, approval.approval_id)
    assert pending.state == "claimed" and pending.public()["retryable"] is True
    a.projection.compare_and_set = original
    after = await agent(a)
    assert after["prompt"] == "Direct." and "definition_revision" not in after
    # The retry detects the change before writing; the selected successor keeps its claim
    # (recorded as selected, never as releasable) for operator inspection.
    record = await a.service.apply(r.session, r.agent_id, approval.approval_id)
    assert record.state == "selected" and record.selected_revision_digest
    assert (await agent(a))["prompt"] == "Direct." and "definition_revision" not in await agent(a)
    claims = await r.mongo.get_collection(APPLICATIONS_COLLECTION)
    assert (await claims.find_one({}))["state"] == "selected"


@pytest.mark.asyncio
async def test_a_change_detected_before_selection_selects_nothing(app_env):  # noqa: F811
    a, r = app_env, app_env.r
    approval = await a.approved(prompt="Approved.")
    service = a.make()
    service._execute = AsyncMock(side_effect=RuntimeError("crash after claim"))
    with pytest.raises(RuntimeError):
        await service.apply(r.session, r.agent_id, approval.approval_id)
    # Direct mutation after the claim, before any selection (no updated_at change).
    await r.agents.update_one({"id": r.agent_id}, {"$set": {"prompt": "Direct."}})
    record = await a.service.apply(r.session, r.agent_id, approval.approval_id)
    assert record.state == "failed" and record.selected_revision_digest is None
    assert record.issue_codes == ("revision_application.predecessor_changed",)
    assert await r.revisions.get(KEY.tenant_id, r.agent_id, 2) is None


# --- Phase 2-3: turn-bound definitions ------------------------------------------------------


async def turn(r, name, mode="llm"):
    c = r.c
    event = c.event.model_copy(update={"event_id": name, "response_mode": mode})
    async with c.s.sessions.authenticated(c.s.token) as session:
        return await c.dispatcher.dispatch(session, event)


async def transcript(r):
    return [m.kind for m in await r.c.repository.read_messages("tenant", r.c.conversation.id)]


@pytest.mark.asyncio
async def test_turns_journal_the_definition_they_ran_under(response_env):  # noqa: F811
    r = response_env
    first = await turn(r, "t1")
    (binding,) = first["definitions"]
    assert binding["agent_id"] == "agent" and binding["revision"] is None
    r.definition["prompt"] = "Changed before admission."  # A new turn binds the new one.
    second = await turn(r, "t2")
    assert second["type"] == "chat.response"
    assert second["definitions"][0]["definition_digest"] != binding["definition_digest"]
    assert await turn(r, "t1") == first  # The journal is immutable.


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["llm", "context"])
async def test_a_change_after_admission_is_definition_changed_and_never_executes(
    response_env,  # noqa: F811
    mode,
):
    r = response_env
    gate = r.c.dispatcher.composition
    admit = gate.admit

    async def admit_then_revise(*args, **kwargs):
        admitted = await admit(*args, **kwargs)
        r.definition["prompt"] = "Revised while the turn was admitted."
        return admitted

    gate.admit = admit_then_revise
    result = await turn(r, "t1", mode)
    assert (result["type"], result["code"], result["retryable"]) == (
        "event.error",
        "definition_changed",
        True,
    )
    r.repo.get_response_async.assert_not_awaited()
    assert not r.c.calls and await transcript(r) == []
    # Retryable means a NEW turn; the same event replays its immutable outcome.
    assert await turn(r, "t1", mode) == result
    gate.admit = admit
    assert (await turn(r, "t2", mode))["type"] != "event.error"


@pytest.mark.asyncio
async def test_the_provider_rechecks_immediately_before_inference(response_env):  # noqa: F811
    r = response_env
    gate = r.c.dispatcher.composition
    ensure = gate.ensure_current

    async def pass_then_revise(*args, **kwargs):
        await ensure(*args, **kwargs)
        r.definition["prompt"] = "Revised between the dispatcher and the provider."

    gate.ensure_current = pass_then_revise
    result = await turn(r, "t1")
    assert (result["code"], result["retryable"]) == ("definition_changed", True)
    r.repo.get_response_async.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_change_after_the_effect_keeps_the_committed_outcome(response_env):  # noqa: F811
    r = response_env

    async def respond_then_revise(*args, **kwargs):
        r.definition["prompt"] = "Revised after the model answered."
        return "I remember Orion."

    r.repo.get_response_async.side_effect = respond_then_revise
    result = await turn(r, "t1")
    assert result["type"] == "chat.response"  # Committed under its admitted binding.
    assert await turn(r, "t1") == result and r.repo.get_response_async.await_count == 1


@pytest.mark.asyncio
async def test_an_interrupted_turn_replays_unknown_and_never_reexecutes(response_env):  # noqa: F811
    r = response_env
    c = r.c
    repository = short(c)
    c.dispatcher.conversations = repository
    crashed = c.event.model_copy(update={"event_id": "crashed"})
    status, _ = await repository.begin("tenant", "owner", crashed)
    assert status == "started"
    await asyncio.sleep(LEASE_MS / 1000 + 0.1)  # the owner died mid-turn
    r.definition["prompt"] = "Revised after the crash."
    async with c.s.sessions.authenticated(c.s.token) as session:
        replayed = await c.dispatcher.dispatch(session, crashed)
    assert (replayed["code"], replayed["outcome"]) == ("interrupted", "unknown")
    r.repo.get_response_async.assert_not_awaited()


@pytest.mark.asyncio
async def test_removed_tool_or_mode_and_revoked_grants_are_enforced(response_env):  # noqa: F811
    r = response_env
    r.definition["role_name"] = "listener"  # no compatibility; explicit Tool decides
    r.definition["tools"] = [{"key": "conversation", "config": {"modes": ["context"]}}]
    assert (await turn(r, "t1"))["code"] == "composition_unavailable"  # mode removed
    r.definition["tools"] = []
    assert (await turn(r, "t2", "context"))["code"] == "composition_unavailable"  # Tool removed
    # An unchanged definition never substitutes for current UTU authority.
    r.definition["role_name"] = "general"
    r.definition.pop("tools")
    policy = SynapticAuthorization(grants={("tenant", "agent"): SynapticGrants(["other"])})
    await r.c.e.utu.install_policy(
        "tenant", expected=await r.c.s.store.current_policy("tenant"), authorization=policy
    )
    with pytest.raises(Exception) as denied:
        await turn(r, "t3")
    assert type(denied.value).__name__ == "EventPermissionError"


@pytest.mark.asyncio
async def test_concurrent_turns_on_one_conversation_are_serialized(response_env):  # noqa: F811
    r = response_env
    results = await asyncio.gather(turn(r, "a"), turn(r, "b"))
    kinds = sorted(x["type"] if x["type"] != "event.error" else x["code"] for x in results)
    assert kinds in (["chat.response", "chat.response"], ["busy", "chat.response"])


def test_bindings_are_exact_and_independent_of_storage_metadata():
    definition = {"id": "a", "tenant_id": "t", "runtime_version": "v2", "prompt": "x"}
    binding = bind_definition("t", "a", definition)
    assert bind_definition("t", "a", {**definition, "updated_at": "later", "_id": 1}) == binding
    assert bind_definition("t", "a", {**definition, "prompt": "y"}) != binding
    marked = {**definition, "definition_revision": {"revision": 2, "revision_digest": "d" * 64}}
    assert (
        bind_definition("t", "a", marked).revision,
        bind_definition("t", "a", marked).revision_digest,
    ) == (2, "d" * 64)


# --- Phase 4: history inspection ------------------------------------------------------------


async def history(a):
    reader = MotorReadOnlyMongo((await a.r.mongo.get_collection("coll_agents")).database)
    (summary,) = await _definition_baselines(reader, KEY.tenant_id)
    return summary


@pytest.mark.asyncio
async def test_an_applied_revision_is_verified_history_not_a_creation_conflict(app_env):  # noqa: F811
    a, r = app_env, app_env.r
    before = await agent(a)
    assert (await history(a))["history"] == "baseline_only"
    approval = await a.approved(prompt="Applied.")
    await a.service.apply(r.session, r.agent_id, approval.approval_id)
    summary = await history(a)
    assert (
        summary["status"],
        summary["history"],
        summary["applied_revision"],
        summary["head_revision"],
    ) == (
        "verified",
        "applied",
        2,
        2,
    )
    assert summary["codes"] == [] and summary["runtime"] == "per_turn"
    after = await agent(a)
    assert after["store"] == before["store"]  # creation evidence untouched


@pytest.mark.asyncio
async def test_selected_unapplied_incomplete_and_mutated_histories_are_reported(app_env):  # noqa: F811
    a, r = app_env, app_env.r
    approval = await a.approved(prompt="Selected only.")
    original = a.projection.compare_and_set
    a.projection.compare_and_set = AsyncMock(side_effect=RuntimeError("crash before CAS"))
    with pytest.raises(RuntimeError):
        await a.service.apply(r.session, r.agent_id, approval.approval_id)
    summary = await history(a)
    assert summary["history"] == "selected_unapplied" and summary["status"] == "unverified"
    assert {"revision.selected_unapplied", "application.incomplete"} <= set(summary["codes"])
    a.projection.compare_and_set = original
    await a.service.apply(r.session, r.agent_id, approval.approval_id)
    await r.agents.update_one({"id": r.agent_id}, {"$set": {"prompt": "Direct mutation."}})
    summary = await history(a)
    assert summary["status"] == "conflicted" and "projection.direct_mutation" in summary["codes"]


def test_invalid_chains_and_duplicates_are_conflicts():
    holder = {
        "id": "a",
        "tenant_id": "t",
        "definition_revision": {"revision": 2, "revision_digest": "d" * 64},
    }
    assert "chain.missing" in inspect_definition("t", "a", [holder], [], [])["codes"]
    assert (
        "identity.duplicate"
        in inspect_definition("t", "a", [holder, holder], [{"x": 1}], [])["codes"]
    )
    assert "chain.invalid_record" in inspect_definition("t", "a", [holder], [{"x": 1}], [])["codes"]
    incomplete = [{"agent_id": "a", "application_id": "p", "state": "applied"}]
    summary = inspect_definition("t", "a", [holder], [], incomplete)
    assert {"application.incomplete", "application.missing_evidence"} <= set(summary["codes"])


# --- Phase 5: activity consent --------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_removed_activity_blocks_new_generations_but_not_existing_contexts(env):  # noqa: F811
    e = env
    context, _ = await start(e, "op-start-00000000001")
    e.threads.permits_activity = AsyncMock(return_value=False)  # the definition removed it
    # The existing context stays readable and closable, and its evidence reconciles.
    assert (await e.service.active(e.actor, e.owner)).context_id == context.context_id
    closed = await e.service.close(
        e.actor,
        Attempt("thread-1", "close", 99),
        context_id=context.context_id,
        expected_revision=context.revision,
        reason="completed",
        operation_id="op-close-0000000001",
    )
    assert closed.status == "closed"
    assert await e.service.reconcile(e.actor, e.owner) == 0
    # A new generation under the removed contract is denied; replaying the original start
    # still returns its recorded (now closed) context.
    with pytest.raises(ActivityContractUnavailable):
        await start(e, "op-start-00000000002")
    replayed, created = await start(e, "op-start-00000000001")
    assert not created and replayed.context_id == context.context_id


@pytest.mark.asyncio
async def test_conversation_owner_consults_the_current_definition_only():
    declared = AsyncMock(return_value=frozenset({"agent.creation_interview@1.0.0"}))
    owner = ConversationOwner(conversations=None, authority=None, activities=declared)
    actor = SimpleNamespace(tenant_id="t", principal_id="enki", grants=frozenset())
    assert await owner.permits_activity(actor, "c", "agent.creation_interview@1.0.0")
    assert not await owner.permits_activity(actor, "c", "test.document_review@1.0.0")
    declared.return_value = None  # MIG-FIX-0.1: never unrestricted; not a declaration
    assert not await owner.permits_activity(actor, "c", "anything@1.0.0")
    gate = ConversationGate(
        definitions=SimpleNamespace(
            find_by_id=AsyncMock(
                return_value={
                    "id": "x",
                    "tenant_id": "t",
                    "runtime_version": "v2",
                    "type": "chat",
                    "role_name": "writer",
                    "tools": [{"key": "conversation", "config": {}}],
                }
            )
        )
    )
    assert await gate.declared_activities("t", "x") == frozenset()


# --- Phase 6: runtime instances -------------------------------------------------------------


def test_runtime_instances_hold_no_definition():
    key = AgentInstanceKey("t", "u", "a", "s")
    instance = ManagedAgentV2(key=key, ncsc_connector=AsyncMock())
    fields = set(vars(instance))
    assert not {f for f in fields if "definition" in f or "tool" in f or "prompt" in f}


def test_no_new_grants_policy_or_lifecycle_actions():
    with open("config/genesis.local.json") as file:
        policy = json.load(file)
    assert not [s for g in policy["grants"] for s in g["invoke"] if "revis" in s]
