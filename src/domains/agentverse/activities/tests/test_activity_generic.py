"""AGENT-CTX-0.1, Agent-neutral (disposable MongoDB): a second, distinct activity
contract for another ordinary Agent, with test resolvers and owner. Real repositories,
real Seshat. Concurrency, crash recovery and evidence outbox.

Requires ``OPS2_MONGO_INTEGRATION=1``.
"""

import asyncio
import os
from dataclasses import dataclass
from types import SimpleNamespace

import pytest
import pytest_asyncio

from src.base.entities.user import User
from src.base.operations.v2.admission import AuthorizationProvenance, sha256_hex
from src.domains.agentverse.activities.models import (
    CONTEXTS_COLLECTION,
    ActivityActiveExists,
    ActivityClosed,
    ActivityContractUnavailable,
    ActivityInvalidReferences,
    ActivityNotFound,
    ActivityOperationConflict,
    ActivityReferenceUnavailable,
    ActivityRevisionConflict,
    ActivityStaleOwner,
    Causation,
    OwnerRef,
)
from src.domains.agentverse.activities.registry import (
    ActivityContract,
    ActivityContractRegistry,
    ResolvedResource,
    ResourceResolverRegistry,
    ResourceSlot,
)
from src.domains.agentverse.activities.repository import MongoActivityContextRepository
from src.domains.agentverse.activities.service import ActivityActor, ActivityContextService
from src.domains.agentverse.audit.models import LOGS_COLLECTION
from src.domains.agentverse.audit.repository import LogUnavailable, MongoLogRepository
from src.domains.agentverse.audit.service import CAOSLogService

NABU = "697e8c4b-171a-b672-c90e-f4b6ca5dd05b"
OWNER_KIND = "test.thread"
THREAD_SIGNAL = "test.thread.turn"
EVENTS = ("activity.context.started", "activity.context.switched", "activity.context.closed")


class Documents:
    """A test resource kind owned by (tenant, subject); states editing/final."""

    kind = "test.document"

    def __init__(self) -> None:
        self.items: dict[str, dict] = {}
        self.writes = 0

    async def resolve(self, actor, resource_id):
        item = self.items.get(resource_id)
        if item is None or (item["tenant"], item["owner"]) != (actor.tenant_id, actor.subject_id):
            raise ActivityReferenceUnavailable(self.kind)
        return ResolvedResource(self.kind, resource_id, item["revision"], item["status"])


@dataclass
class Attempt:
    """A test owner attempt (stands in for a conversation turn lease)."""

    owner_id: str
    event_id: str
    fence: int
    live: bool = True


class Threads:
    """A non-conversation owner kind: its own audit signal, no ``chat.response``."""

    kind = OWNER_KIND
    audit_signal = THREAD_SIGNAL
    audit_version = "1.0.0"

    def __init__(self) -> None:
        self.members: dict[str, set[str]] = {"thread-1": {"owner"}, "thread-2": {"owner"}}
        # Owner kinds consent explicitly (MIG-FIX-0.1); this one declares one contract.
        self.declared = frozenset({"test.document_review@1.0.0"})

    async def permits_activity(self, actor, owner_id, activity):
        return activity in self.declared

    async def authorize(self, actor, owner_id):
        if actor.subject_id not in self.members.get(owner_id, set()):
            raise ActivityNotFound("Activity context not found")

    async def causation(self, actor, owner_id, attempt, operation_id):
        if not isinstance(attempt, Attempt) or attempt.owner_id != owner_id or not attempt.live:
            raise ActivityStaleOwner("stale")
        return Causation(event_id=attempt.event_id, fence=attempt.fence, operation_id=operation_id)


REVIEW = ActivityContract(
    id="test.document_review",
    version="1.0.0",
    title="Document review",
    description="Review one document.",
    owner_kinds=frozenset({OWNER_KIND}),
    slots=(
        ResourceSlot(
            name="document",
            kind="test.document",
            required=True,
            update="immutable",
            start_statuses=frozenset({"editing"}),
            update_statuses=frozenset({"editing", "final"}),
        ),
    ),
)


def actor(subject="owner", principal=NABU, tenant="tenant"):
    return ActivityActor(
        tenant_id=tenant,
        subject_id=subject,
        principal_id=principal,
        session_id="session",
        requester=User(id=subject, tenant_id=tenant),
        # CONVERSATION-0.1A: chat.response belongs to the conversation.v2 owner adapter,
        # not to every owner kind. This owner needs no conversational grant.
        grants=frozenset(),
        provenance=lambda signal: AuthorizationProvenance(
            authority="utu",
            tenant_id=tenant,
            principal_id=principal,
            subject_id=subject,
            policy_version="v1",
            evidence_digest=sha256_hex({"signal": signal}),
        ),
    )


@pytest_asyncio.fixture
async def env():
    if os.environ.get("OPS2_MONGO_INTEGRATION") != "1":
        pytest.skip("Set OPS2_MONGO_INTEGRATION=1 for real Mongo activity tests")
    from src.base.infrastructure.db.mongoDB.isolation import isolated_mongo_repository

    async with isolated_mongo_repository("activities") as mongo:
        documents = Documents()
        resolvers = ResourceResolverRegistry()
        resolvers.register(documents)
        resolvers.freeze()
        threads = Threads()
        contracts = ActivityContractRegistry(resolvers, frozenset({OWNER_KIND}))
        contracts.register(REVIEW)
        contracts.freeze()
        repository = MongoActivityContextRepository(mongo)
        audit = CAOSLogService(repository=MongoLogRepository(mongo))
        service = ActivityContextService(
            repository=repository,
            contracts=contracts,
            resolvers=resolvers,
            owners={OWNER_KIND: threads},
            audit=audit,
        )
        for name in ("doc-1", "doc-2", "doc-3"):
            documents.items[name] = {
                "tenant": "tenant",
                "owner": "owner",
                "revision": 1,
                "status": "editing",
            }
        yield SimpleNamespace(
            mongo=mongo,
            documents=documents,
            threads=threads,
            repository=repository,
            audit=audit,
            service=service,
            actor=actor(),
            owner=OwnerRef(kind=OWNER_KIND, id="thread-1"),
            fences=iter(range(1, 10_000)),
        )


def attempt(e, owner_id="thread-1"):
    fence = next(e.fences)
    return Attempt(owner_id, f"event-{fence}", fence)


async def start(e, op, doc="doc-1", at=None, who=None):
    return await e.service.start(
        who or e.actor,
        at or attempt(e),
        owner=e.owner,
        activity="test.document_review@1.0.0",
        references={"document": doc},
        operation_id=op,
    )


async def switch(e, context, op, doc="doc-2", at=None):
    return await e.service.switch(
        e.actor,
        at or attempt(e),
        context_id=context.context_id,
        expected_revision=context.revision,
        activity="test.document_review@1.0.0",
        references={"document": doc},
        operation_id=op,
    )


async def events(e):
    db = await e.mongo.get_collection(LOGS_COLLECTION)
    records = await db.find({"event_type": {"$in": EVENTS}}).to_list(None)
    # Evidence names the owner kind's own signal, never a universal conversation signal.
    assert {(r["target"]["signal"], r["target"]["version"]) for r in records} <= {
        (THREAD_SIGNAL, "1.0.0")
    }
    assert {r["authorization"]["evidence_digest"] for r in records} <= {
        sha256_hex({"signal": THREAD_SIGNAL})
    }
    return sorted(r["event_type"] for r in records)


async def pending(e):
    db = await e.mongo.get_collection(CONTEXTS_COLLECTION)
    return sum(len(d.get("pending_evidence") or []) for d in await db.find({}).to_list(None))


@pytest.mark.asyncio
async def test_a_second_contract_and_agent_use_the_same_service_without_special_cases(env):
    e = env
    assert await e.service.active(e.actor, e.owner) is None
    context, created = await start(e, "op-start-00000000001")
    assert created and context.activity.key == "test.document_review@1.0.0"
    assert context.actor.principal_id == NABU and context.references["document"].id == "doc-1"
    assert (await e.service.active(e.actor, e.owner)).context_id == context.context_id
    # Typed slots and exact versions are enforced.
    for bad in ({"document": "doc-1", "extra": "x"}, {}):
        with pytest.raises(ActivityInvalidReferences):
            await e.service.start(
                e.actor,
                attempt(e, "thread-2"),
                owner=OwnerRef(kind=OWNER_KIND, id="thread-2"),
                activity="test.document_review@1.0.0",
                references=bad,
                operation_id="op-bad-refs-000000001",
            )
    for activity in (
        "test.document_review@1.0.1",
        "test.document_review",
        "agent.creation_interview@1.0.0",
    ):
        with pytest.raises(ActivityContractUnavailable):
            await e.service.start(
                e.actor,
                attempt(e, "thread-2"),
                owner=OwnerRef(kind=OWNER_KIND, id="thread-2"),
                activity=activity,
                references={"document": "doc-1"},
                operation_id="op-bad-contract-00001",
            )
    assert await events(e) == ["activity.context.started"] and await pending(e) == 0


@pytest.mark.asyncio
async def test_isolation_and_owner_authority(env):
    e = env
    context, _ = await start(e, "op-start-00000000001")
    for other in (actor(subject="someone"), actor(principal="thoth"), actor(tenant="other")):
        with pytest.raises(ActivityNotFound):
            await e.service.get(other, context.context_id)
    # Another human of the thread sees only their own (empty) scope.
    e.threads.members["thread-1"].add("someone")
    assert await e.service.active(actor(subject="someone"), e.owner) is None
    # Losing owner access hides the context.
    e.threads.members["thread-1"].discard("owner")
    with pytest.raises(ActivityNotFound):
        await e.service.get(e.actor, context.context_id)
    # Foreign or missing resources are indistinguishable.
    e.threads.members["thread-1"].add("owner")
    e.documents.items["foreign"] = {
        "tenant": "tenant",
        "owner": "x",
        "revision": 1,
        "status": "editing",
    }
    e.threads.members["thread-2"] = {"owner"}
    errors = set()
    for doc in ("foreign", "missing"):
        with pytest.raises(ActivityReferenceUnavailable) as raised:
            await e.service.start(
                e.actor,
                attempt(e, "thread-2"),
                owner=OwnerRef(kind=OWNER_KIND, id="thread-2"),
                activity="test.document_review@1.0.0",
                references={"document": doc},
                operation_id=f"op-foreign-{doc:>10}".replace(" ", "0"),
            )
        errors.add((raised.value.code, raised.value.slot))
    assert errors == {("activity.reference_unavailable", "document")}


@pytest.mark.asyncio
async def test_idempotency_cas_and_stale_owners(env):
    e = env
    context, _ = await start(e, "op-start-00000000001")
    assert await start(e, "op-start-00000000001") == (context, False)
    with pytest.raises(ActivityOperationConflict):
        await start(e, "op-start-00000000001", doc="doc-2")
    with pytest.raises(ActivityActiveExists):
        await start(e, "op-start-00000000002")
    at = attempt(e)
    advanced = await e.service.advance(
        e.actor,
        at,
        context_id=context.context_id,
        expected_revision=1,
        operation_id="op-advance-000000001",
    )
    assert advanced.revision == 2
    assert (
        await e.service.advance(
            e.actor,
            at,
            context_id=context.context_id,
            expected_revision=1,
            operation_id="op-advance-000000001",
        )
        == advanced
    )
    with pytest.raises(ActivityRevisionConflict):
        await e.service.advance(
            e.actor,
            at,
            context_id=context.context_id,
            expected_revision=1,
            operation_id="op-advance-000000002",
        )
    # An immutable slot never changes within a generation.
    with pytest.raises(ActivityInvalidReferences):
        await e.service.advance(
            e.actor,
            attempt(e),
            context_id=context.context_id,
            expected_revision=2,
            operation_id="op-advance-000000003",
            references={"document": "doc-2"},
        )
    # Stale owner attempts: lost ownership, or older than the last writer.
    old = Attempt("thread-1", "event-old", 1)
    with pytest.raises(ActivityStaleOwner):
        await e.service.advance(
            e.actor,
            old,
            context_id=context.context_id,
            expected_revision=2,
            operation_id="op-advance-000000004",
        )
    dead = attempt(e)
    dead.live = False
    with pytest.raises(ActivityStaleOwner):
        await e.service.advance(
            e.actor,
            dead,
            context_id=context.context_id,
            expected_revision=2,
            operation_id="op-advance-000000005",
        )
    # Concurrent CAS updates: exactly one wins.
    at = attempt(e)
    results = await asyncio.gather(
        *(
            e.service.advance(
                e.actor,
                at,
                context_id=context.context_id,
                expected_revision=2,
                operation_id=f"op-race-advance-{i:04d}",
            )
            for i in range(5)
        ),
        return_exceptions=True,
    )
    assert sum(not isinstance(r, Exception) for r in results) == 1
    assert all(isinstance(r, ActivityRevisionConflict) for r in results if isinstance(r, Exception))


@pytest.mark.asyncio
async def test_concurrent_starts_and_switches_never_create_two_active_generations(env):
    e = env
    starts = await asyncio.gather(
        *(start(e, f"op-race-start-{i:06d}") for i in range(6)), return_exceptions=True
    )
    winners = [r for r in starts if isinstance(r, tuple)]
    assert len(winners) == 1
    assert all(isinstance(r, ActivityActiveExists) for r in starts if not isinstance(r, tuple))
    first = winners[0][0]
    switches = await asyncio.gather(
        *(switch(e, first, f"op-race-switch-{i:05d}", doc=f"doc-{2 + i % 2}") for i in range(6)),
        return_exceptions=True,
    )
    successors = [r for r in switches if not isinstance(r, Exception)]
    assert len(successors) == 1, switches
    assert all(
        isinstance(r, (ActivityActiveExists, ActivityClosed))
        for r in switches
        if isinstance(r, Exception)
    )
    # A concurrent start during a switch can never orphan it (start needs no active one).
    history = await e.service.history(e.actor, e.owner)
    assert [(c.generation, c.status) for c in history] == [(1, "closed"), (2, "active")]
    db = await e.mongo.get_collection(CONTEXTS_COLLECTION)
    assert await db.count_documents({"status": "active"}) == 1
    assert sorted(await events(e)) == ["activity.context.started", "activity.context.switched"]


@pytest.mark.asyncio
async def test_crash_after_closure_before_successor_is_completed_by_replay_or_reconcile(env):
    e = env
    first, _ = await start(e, "op-start-00000000001")
    original = e.repository.insert
    calls = []

    async def crash(context):
        calls.append(context.context_id)
        raise RuntimeError("process died")

    e.repository.insert = crash
    at = attempt(e)
    with pytest.raises(RuntimeError):
        await switch(e, first, "op-switch-0000000001", at=at)
    e.repository.insert = original
    # The predecessor is closed and the successor is decided (head intent) but missing.
    closed = await e.repository.get("tenant", first.context_id)
    assert closed.status == "closed" and closed.closed_reason == "switched"
    head = await e.repository.head("tenant", first.scope_key)
    assert head.intent is not None and head.intent.operation_id == "op-switch-0000000001"
    # Unresolved successor intent: decided and reported active, but not materialized.
    decided = await e.service.current(e.actor, e.owner)
    assert decided.context.context_id == calls[0] and decided.context.generation == 2
    assert (decided.materialized, decided.reconciliation_pending) == (False, True)
    assert (await e.service.active(e.actor, e.owner)).context_id == calls[0]
    assert await e.repository.get("tenant", calls[0]) is None  # Reads never complete it.
    # No competing successor: a new start or another switch is refused meanwhile.
    with pytest.raises(ActivityActiveExists):
        await start(e, "op-start-00000000009")
    # Replaying the original operation completes exactly the decided successor.
    successor = await switch(e, first, "op-switch-0000000001", at=at)
    assert successor.generation == 2 and successor.context_id == calls[0]
    assert await switch(e, first, "op-switch-0000000001", at=at) == successor
    head = await e.repository.head("tenant", first.scope_key)
    assert head.intent is None and head.active_context_id == successor.context_id
    settled = await e.service.current(e.actor, e.owner)
    assert settled.context == successor
    assert (settled.materialized, settled.reconciliation_pending) == (True, False)
    # And without a replay, any authorized reconciliation completes it too.
    e.repository.insert = crash
    with pytest.raises(RuntimeError):
        await switch(e, successor, "op-switch-0000000002", doc="doc-3")
    e.repository.insert = original
    assert await e.service.reconcile(e.actor, e.owner) == 0
    third = await e.service.active(e.actor, e.owner)
    assert third.generation == 3 and third.references["document"].id == "doc-3"
    assert sorted(await events(e)) == sorted(
        ["activity.context.started", "activity.context.switched", "activity.context.switched"]
    )


@pytest.mark.asyncio
async def test_close_then_restart_preserves_history(env):
    e = env
    first, _ = await start(e, "op-start-00000000001")
    at = attempt(e)
    closed = await e.service.close(
        e.actor,
        at,
        context_id=first.context_id,
        expected_revision=1,
        operation_id="op-close-00000000001",
        reason="completed",
    )
    assert closed.status == "closed" and await e.service.active(e.actor, e.owner) is None
    assert (
        await e.service.close(
            e.actor,
            at,
            context_id=first.context_id,
            expected_revision=1,
            operation_id="op-close-00000000001",
            reason="completed",
        )
        == closed
    )
    with pytest.raises(ActivityClosed):
        await e.service.advance(
            e.actor,
            attempt(e),
            context_id=first.context_id,
            expected_revision=2,
            operation_id="op-advance-000000001",
        )
    second, _ = await start(e, "op-start-00000000002", doc="doc-2")
    assert second.generation == 2 and second.previous_context_id == first.context_id
    assert [c.status for c in await e.service.history(e.actor, e.owner)] == ["closed", "active"]
    assert sorted(await events(e)) == sorted(
        ["activity.context.started", "activity.context.closed", "activity.context.started"]
    )


@pytest.mark.asyncio
async def test_evidence_outbox_survives_crashes_and_reconciles_exactly_once(env):
    e = env
    original_append = e.audit.append

    async def seshat_down(event, *, authorization):
        raise LogUnavailable("Log store is unavailable")

    # 1. Crash between the transition and the Seshat write: transition committed, the
    #    obligation stays pending and discoverable.
    e.audit.append = seshat_down
    first, _ = await start(e, "op-start-00000000001")
    e.audit.append = original_append
    assert await pending(e) == 1 and await events(e) == []
    assert [c.context_id for c in await e.repository.with_pending_evidence("tenant")] == [
        first.context_id
    ]
    # Reconciliation by any authorized actor of the scope (no client retry).
    assert await e.service.reconcile(e.actor, e.owner) == 0
    assert await events(e) == ["activity.context.started"] and await pending(e) == 0

    # 2. Crash between the Seshat write and settling the obligation.
    original_replace = e.repository.replace

    async def settle_crash(previous, following):
        if following.revision == previous.revision:
            raise RuntimeError("process died")
        return await original_replace(previous, following)

    e.repository.replace = settle_crash
    with pytest.raises(RuntimeError):
        await e.service.close(
            e.actor,
            attempt(e),
            context_id=first.context_id,
            expected_revision=1,
            operation_id="op-close-00000000001",
            reason="abandoned",
        )
    e.repository.replace = original_replace
    assert await pending(e) == 1
    assert sorted(await events(e)) == ["activity.context.closed", "activity.context.started"]
    # 3. Repeated and concurrent reconciliation: no duplicate record.
    results = await asyncio.gather(*(e.service.reconcile(e.actor, e.owner) for _ in range(4)))
    assert results[-1] == 0 and await pending(e) == 0
    assert sorted(await events(e)) == ["activity.context.closed", "activity.context.started"]
    assert await e.service.reconcile(e.actor, e.owner) == 0


@pytest.mark.asyncio
async def test_storage_grants_no_resource_authority_and_writes_no_resource(env):
    e = env
    await start(e, "op-start-00000000001")
    assert e.documents.writes == 0
    # The service holds no generic resource handle: only frozen, read-only resolvers.
    assert (
        not {"_store", "_exchange", "_drafts", "_manuscripts", "_proposals"}
        & vars(e.service).keys()
    )
    with pytest.raises(Exception):  # noqa: B017 - frozen registries refuse anything
        e.service._resolvers.register(Documents())
