"""AGENT-CTX-0.1 with the registered ``agent.creation_interview@1.0.0`` contract on
disposable Mongo and Redis: real UTU session, an ordinary Agent principal (Enki), a V2
conversation, CONV-REC-0.1 turn leases, Manuscript sessions bound to real drafts, a real
proposal and Seshat. Ported from ENKI-CTX-0.1; no Enki-specific code path.

Requires ``OPS2_MONGO_INTEGRATION=1`` and ``UTU_TEST_REDIS_URL``.
"""

import asyncio
import dataclasses
from types import SimpleNamespace

import pytest
import pytest_asyncio

from src.domains.agentverse.activities.models import (
    CONTEXTS_COLLECTION,
    ActivityActiveExists,
    ActivityClosed,
    ActivityNotFound,
    ActivityOperationConflict,
    ActivityReferenceUnavailable,
    ActivityRevisionConflict,
    ActivityStaleOwner,
    OwnerRef,
)
from src.domains.agentverse.activities.service import ActivityActor, view
from src.domains.agentverse.agents.creation_drafts.capability import submit_proposal
from src.domains.agentverse.agents.creation_drafts.models import (
    DRAFTS_COLLECTION,
    AgentDraftRevision,
    RevisionCause,
)
from src.domains.agentverse.audit.repository import LogUnavailable
from src.domains.agentverse.dependencies.genesis_local import ENKI, OWNER, TENANT
from src.domains.agentverse.entities.conversation_v2 import ClientEventV2, ConversationV2
from src.domains.agentverse.manuscripts.sessions import SESSIONS_COLLECTION
from src.domains.agentverse.manuscripts.tests.test_manuscript_capability_integration import (
    command,
)
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey
from src.domains.agentverse.tests.agents.test_creation_drafts_integration import (
    local_app,  # noqa: F401
    snapshot,
)
from src.domains.agentverse.tests.agents.test_creation_review_integration import creator
from src.domains.agentverse.tests.agents.test_draft_proposal_capability_integration import (
    in_session,
    submission,
)
from src.domains.agentverse.tests.agents.test_recursive_genesis_integration import with_enki
from src.domains.agentverse.tools.manuscript_tool import ManuscriptTool

DRAFTS = f"/api/v2/agentverse/agents/{ENKI}/creation-drafts"
INTERVIEW = "agent.creation_interview@1.0.0"
EVENTS = ("activity.context.started", "activity.context.switched", "activity.context.closed")


def body(name: str) -> dict:
    return {
        "contract": "caos.agent_creation_draft",
        "schema_version": 1,
        "resource_type": "agent",
        "creation": {"name": name, "type": "chat"},
    }


@pytest_asyncio.fixture
async def env(local_app):  # noqa: F811
    s = await with_enki(local_app)
    av = s.av
    client = await creator(s)
    session_id = (await client.get("/api/v2/agentverse/session")).json()["session_id"]
    conversation = await av.conversation_repository_v2().create(
        ConversationV2(tenant_id=TENANT, member_user_ids=(OWNER,), agent_ids=(ENKI,))
    )
    try:
        async with in_session(s, client):
            authority = av.utu_authority()
            enki = await authority.resolve_agent_principal(
                AgentInstanceKey(TENANT, OWNER, ENKI, session_id)
            )
            yield SimpleNamespace(
                s=s,
                av=av,
                client=client,
                authority=authority,
                enki=enki,
                actor=await ActivityActor.from_principal(authority, enki),
                contexts=s.app.state.activity_contexts,
                conversations=av.conversation_repository_v2(),
                conversation=conversation.id,
                owner=OwnerRef(kind="conversation.v2", id=conversation.id),
                tool=ManuscriptTool(),
                turns=iter(range(1, 10_000)),
            )
    finally:
        await client.aclose()


async def draft(e, name="Contract Summarizer", key=None):
    response = await e.client.post(
        DRAFTS, json=body(name), headers={"Idempotency-Key": key or f"ctx-draft-{name[:6]}-0001"}
    )
    assert response.status_code in (200, 201), response.text
    return response.json()["draft_id"]


async def interview(e, draft_id, key="ctx-interview-00001"):
    result = await e.tool.invoke(
        authority=e.authority,
        exchange=e.av.synaptic_exchange(),
        principal=e.enki,
        op="start",
        command=command("start", idempotency_key=key, draft_id=draft_id),
    )
    assert result["ok"], result
    return result["session"]["session_id"]


async def turn(e):
    """A real, live CONV-REC-0.1 lease for a new turn in the conversation."""
    event = ClientEventV2(
        conversation_id=e.conversation,
        event_id=f"ctx-turn-{next(e.turns)}",
        type="chat.message",
        agent_ids=(ENKI,),
        message="hi",
    )
    status, lease = await e.conversations.begin(TENANT, OWNER, event)
    assert status == "started", status
    return event, lease


async def finish(e, event, lease):
    await e.conversations.finish(TENANT, OWNER, event, {"type": "done"}, lease=lease)


async def start(e, lease, session_id, draft_id, op, actor=None):
    return await e.contexts.start(
        actor or e.actor,
        lease,
        owner=e.owner,
        activity=INTERVIEW,
        references={"manuscript_session": session_id, "draft": draft_id},
        operation_id=op,
    )


async def context_events(e):
    return await e.s.mongo.db["coll_logs"].count_documents({"event_type": {"$in": EVENTS}})


@pytest.mark.asyncio
async def test_a_normal_conversation_has_no_context(env):
    e = env
    assert await e.contexts.active(e.actor, e.owner) is None
    assert await e.contexts.history(e.actor, e.owner) == []
    assert await e.s.mongo.db[CONTEXTS_COLLECTION].count_documents({}) == 0


@pytest.mark.asyncio
async def test_interview_lifecycle_idempotency_and_evidence(env):
    e = env
    draft_id = await draft(e)
    session_id = await interview(e, draft_id)
    sessions_before = await e.s.mongo.db[SESSIONS_COLLECTION].find({}).to_list(None)
    drafts_before = await e.s.mongo.db[DRAFTS_COLLECTION].find({}).to_list(None)
    agents_before = (await snapshot(e.s))["agents"]

    event, lease = await turn(e)
    context, created = await start(e, lease, session_id, draft_id, "op-start-0000000001")
    assert created and context.generation == 1 and context.status == "active"
    assert context.activity.key == INTERVIEW and context.actor.principal_id == ENKI
    assert context.references["manuscript_session"].id == session_id
    assert context.references["draft"].id == draft_id
    assert context.created_by.event_id == event.event_id and context.created_by.fence == lease.fence
    stored = await e.s.mongo.db[CONTEXTS_COLLECTION].find_one({"_id": context.context_id})
    text = str(stored)
    for forbidden in ("Contract Summarizer", "permit", "token", "hi'", "answers", "prompt"):
        assert forbidden not in text, forbidden
    # The requester projection omits actor internals, causation and evidence.
    projected = view(context).model_dump()
    assert not {"actor", "created_by", "last_owner", "applied", "pending_evidence"} & set(projected)
    assert await start(e, lease, session_id, draft_id, "op-start-0000000001") == (context, False)
    with pytest.raises(ActivityOperationConflict):
        await start(e, lease, session_id, "another-draft", "op-start-0000000001")
    with pytest.raises(ActivityActiveExists):
        await start(e, lease, session_id, draft_id, "op-start-0000000002")
    assert await e.contexts.active(e.actor, e.owner) == context

    # A real proposal reference, resolved through its owner; idempotent advance.
    receipt = await submit_proposal(
        e.authority, e.av.synaptic_exchange(), e.enki, submission(draft_id)
    )
    assert receipt["ok"], receipt
    advanced = await e.contexts.advance(
        e.actor,
        lease,
        context_id=context.context_id,
        expected_revision=1,
        operation_id="op-advance-00000001",
        references={"proposal": receipt["proposal_id"]},
    )
    assert advanced.revision == 2 and advanced.references["proposal"].id == receipt["proposal_id"]
    assert (
        await e.contexts.advance(
            e.actor,
            lease,
            context_id=context.context_id,
            expected_revision=1,
            operation_id="op-advance-00000001",
            references={"proposal": receipt["proposal_id"]},
        )
        == advanced
    )
    with pytest.raises(ActivityRevisionConflict):
        await e.contexts.advance(
            e.actor,
            lease,
            context_id=context.context_id,
            expected_revision=1,
            operation_id="op-advance-00000002",
        )
    await finish(e, event, lease)

    event, lease = await turn(e)
    second_draft = await draft(e, "Second Agent", key="ctx-draft-second-01")
    second_session = await interview(e, second_draft, key="ctx-interview-00002")

    async def switch():
        return await e.contexts.switch(
            e.actor,
            lease,
            context_id=context.context_id,
            expected_revision=2,
            activity=INTERVIEW,
            references={"manuscript_session": second_session, "draft": second_draft},
            operation_id="op-switch-000000001",
        )

    switched = await switch()
    assert switched.generation == 2 and switched.previous_context_id == context.context_id
    assert switched.origin == "switch" and await switch() == switched
    history = await e.contexts.history(e.actor, e.owner)
    assert [(c.generation, c.status, c.closed_reason) for c in history] == [
        (1, "closed", "switched"),
        (2, "active", None),
    ]

    async def close():
        return await e.contexts.close(
            e.actor,
            lease,
            context_id=switched.context_id,
            expected_revision=1,
            operation_id="op-close-0000000001",
            reason="completed",
        )

    closed = await close()
    assert closed.status == "closed" and closed.closed_reason == "completed"
    assert await close() == closed
    with pytest.raises(ActivityClosed):
        await e.contexts.advance(
            e.actor,
            lease,
            context_id=closed.context_id,
            expected_revision=2,
            operation_id="op-advance-00000003",
        )
    assert await e.contexts.active(e.actor, e.owner) is None
    assert len(await e.contexts.history(e.actor, e.owner)) == 2
    await finish(e, event, lease)

    logs = (
        await e.s.mongo.db["coll_logs"]
        .find({"event_type": {"$in": EVENTS}}, {"_id": 0})
        .to_list(None)
    )
    assert sorted(r["event_type"] for r in logs) == sorted(EVENTS)
    assert {r["subject"]["resource_type"] for r in logs} == {"activity_context"}
    evidence = str(logs)
    for forbidden in ("Contract Summarizer", "Second Agent", "hi'", "answers", "enki.context"):
        assert forbidden not in evidence
    sessions_after = await e.s.mongo.db[SESSIONS_COLLECTION].find({"_id": session_id}).to_list(None)
    assert sessions_after == [d for d in sessions_before if d["_id"] == session_id]
    drafts_after = await e.s.mongo.db[DRAFTS_COLLECTION].find({"_id": draft_id}).to_list(None)
    assert drafts_after == [d for d in drafts_before if d["_id"] == draft_id]
    assert (await snapshot(e.s))["agents"] == agents_before


@pytest.mark.asyncio
async def test_foreign_scopes_and_lost_membership_are_indistinguishable(env):
    e = env
    draft_id = await draft(e)
    session_id = await interview(e, draft_id)
    _, lease = await turn(e)
    context, _ = await start(e, lease, session_id, draft_id, "op-start-0000000001")
    other_tenant = dataclasses.replace(e.actor, tenant_id="other-tenant")
    other_human = dataclasses.replace(e.actor, subject_id="someone")
    other_agent = dataclasses.replace(e.actor, principal_id="00000000-0000-4000-8000-000000000006")
    no_turn_grant = dataclasses.replace(e.actor, grants=frozenset({"chat.context"}))
    for other in (other_tenant, other_human, other_agent, no_turn_grant):
        with pytest.raises(ActivityNotFound):
            await e.contexts.get(other, context.context_id)
    assert await e.contexts.active(other_human, e.owner) is None
    for other in (other_tenant, other_agent, no_turn_grant):
        with pytest.raises(ActivityNotFound):
            await e.contexts.active(other, e.owner)
    with pytest.raises(ActivityNotFound):
        await e.contexts.get(e.actor, "00000000-0000-4000-8000-00000000dead")
    keys = e.conversations._keys(TENANT, e.conversation)
    document = await e.conversations.get(TENANT, e.conversation)
    await e.conversations.repository.eval(
        "return redis.call('SET', KEYS[1], ARGV[1])",
        keys=keys[:1],
        args=[document.model_copy(update={"member_user_ids": ("someone",)}).model_dump_json()],
    )
    for read in (e.contexts.get(e.actor, context.context_id), e.contexts.active(e.actor, e.owner)):
        with pytest.raises(ActivityNotFound):
            await read


@pytest.mark.asyncio
async def test_references_are_resolved_through_their_owners(env):
    e = env
    _, lease = await turn(e)
    draft_id = await draft(e)
    with pytest.raises(ActivityReferenceUnavailable) as raised:
        await start(
            e, lease, "00000000-0000-4000-8000-00000000beef", draft_id, "op-missing-0000001"
        )
    assert (raised.value.code, raised.value.slot) == (
        "activity.reference_unavailable",
        "manuscript_session",
    )
    # A session without an attached draft is inconsistent with the draft slot.
    plain = await e.tool.invoke(
        authority=e.authority,
        exchange=e.av.synaptic_exchange(),
        principal=e.enki,
        op="start",
        command=command("start", idempotency_key="ctx-no-draft-000001"),
    )
    with pytest.raises(ActivityReferenceUnavailable) as raised:
        await start(e, lease, plain["session"]["session_id"], draft_id, "op-no-draft-0000001")
    assert raised.value.code == "activity.reference_inconsistent"
    session_id = await interview(e, draft_id)
    other_draft = await draft(e, "Other Draft", key="ctx-draft-other-001")
    with pytest.raises(ActivityReferenceUnavailable) as raised:
        await start(e, lease, session_id, other_draft, "op-mismatch-000001")
    assert raised.value.code == "activity.reference_inconsistent"
    # The draft freezes after the session started: not editable, nothing bound.
    repository = e.av.agent_draft_repository()
    head = await repository.current(TENANT, draft_id)
    await repository.commit(
        AgentDraftRevision.build(
            **{
                **head.model_dump(exclude={"revision_digest", "content_digest"}),
                "revision": head.revision + 1,
                "status": "approved",
                "parent_digest": head.revision_digest,
                "cause": RevisionCause(
                    kind="approval", approval_id="ap-1", review_digest="1" * 64, reviewed_revision=1
                ),
            }
        )
    )
    with pytest.raises(ActivityReferenceUnavailable) as raised:
        await start(e, lease, session_id, draft_id, "op-frozen-draft-001")
    assert (raised.value.code, raised.value.slot) == ("activity.reference_state", "draft")
    # Another human's actor cannot resolve the owner's session or draft.
    stranger = dataclasses.replace(e.actor, subject_id="someone")
    resolver = e.contexts._resolvers.get("manuscript.session")
    with pytest.raises(ActivityReferenceUnavailable):
        await resolver.resolve(stranger, session_id)
    assert await e.s.mongo.db[CONTEXTS_COLLECTION].count_documents({}) == 0


@pytest.mark.asyncio
async def test_concurrent_writers_and_stale_turns(env):
    e = env
    draft_id = await draft(e)
    session_id = await interview(e, draft_id)
    event, lease = await turn(e)
    starts = await asyncio.gather(
        *(start(e, lease, session_id, draft_id, f"op-race-start-{i:05d}") for i in range(4)),
        return_exceptions=True,
    )
    winners = [r for r in starts if isinstance(r, tuple)]
    assert len(winners) == 1 and all(
        isinstance(r, ActivityActiveExists) for r in starts if not isinstance(r, tuple)
    )
    context = winners[0][0]
    updates = await asyncio.gather(
        *(
            e.contexts.advance(
                e.actor,
                lease,
                context_id=context.context_id,
                expected_revision=1,
                operation_id=f"op-race-adv-{i:07d}",
            )
            for i in range(4)
        ),
        return_exceptions=True,
    )
    assert sum(not isinstance(r, Exception) for r in updates) == 1
    assert all(isinstance(r, ActivityRevisionConflict) for r in updates if isinstance(r, Exception))

    await e.conversations.repository.redis.pexpire(
        e.conversations._keys(TENANT, e.conversation)[2], 1
    )
    await asyncio.sleep(0.01)
    newer_event, newer = await turn(e)
    with pytest.raises(ActivityStaleOwner):
        await e.contexts.advance(
            e.actor,
            lease,
            context_id=context.context_id,
            expected_revision=2,
            operation_id="op-stale-advance-01",
        )
    with pytest.raises(ActivityStaleOwner):
        await e.contexts.close(
            e.actor,
            lease,
            context_id=context.context_id,
            expected_revision=2,
            operation_id="op-stale-close-0001",
            reason="abandoned",
        )
    for foreign in (
        dataclasses.replace(newer, conversation_id="other"),
        dataclasses.replace(newer, identity='["someone", "x"]'),
    ):
        with pytest.raises(ActivityStaleOwner):
            await e.contexts.advance(
                e.actor,
                foreign,
                context_id=context.context_id,
                expected_revision=2,
                operation_id="op-foreign-turn-01",
            )
    advanced = await e.contexts.advance(
        e.actor,
        newer,
        context_id=context.context_id,
        expected_revision=2,
        operation_id="op-newer-advance-1",
    )
    assert advanced.last_owner.fence == newer.fence > lease.fence
    original = e.conversations.owns

    async def always(_lease):
        return True

    e.conversations.owns = always
    try:
        with pytest.raises(ActivityStaleOwner):
            await e.contexts.advance(
                e.actor,
                lease,
                context_id=context.context_id,
                expected_revision=3,
                operation_id="op-old-fence-00001",
            )
    finally:
        e.conversations.owns = original
    status, recorded = await e.conversations.begin(
        TENANT,
        OWNER,
        ClientEventV2(
            conversation_id=e.conversation,
            event_id=event.event_id,
            type="chat.message",
            agent_ids=(ENKI,),
            message="hi",
        ),
    )
    assert (status, recorded["code"]) == ("replay", "interrupted")
    await finish(e, newer_event, newer)


@pytest.mark.asyncio
async def test_missing_evidence_is_reconciled_without_a_client_retry(env):
    e = env
    draft_id = await draft(e)
    session_id = await interview(e, draft_id)
    _, lease = await turn(e)
    audit = e.contexts._audit
    original = audit.append

    async def failing(event, *, authorization):
        raise LogUnavailable("Log store is unavailable")

    audit.append = failing
    try:
        context, created = await start(e, lease, session_id, draft_id, "op-start-audit-001")
    finally:
        audit.append = original
    # The transition committed with its obligation; evidence is pending and discoverable.
    assert created and await context_events(e) == 0
    assert len(context.pending_evidence) == 1
    assert await e.s.mongo.db[CONTEXTS_COLLECTION].count_documents({}) == 1
    # Any authorized reconciliation of the scope records it exactly once.
    assert await e.contexts.reconcile(e.actor, e.owner) == 0
    assert await context_events(e) == 1
    assert await e.contexts.reconcile(e.actor, e.owner) == 0
    assert await start(e, lease, session_id, draft_id, "op-start-audit-001") == (
        await e.contexts.get(e.actor, context.context_id),
        False,
    )
    assert await context_events(e) == 1


@pytest.mark.asyncio
async def test_refresh_is_read_only(env):
    e = env
    draft_id = await draft(e)
    session_id = await interview(e, draft_id)
    _, lease = await turn(e)
    context, _ = await start(e, lease, session_id, draft_id, "op-start-0000000001")
    before = await e.s.mongo.db[CONTEXTS_COLLECTION].find({}).to_list(None)
    refreshed = await e.contexts.refresh(e.actor, context.context_id)
    assert refreshed.context == context and not refreshed.unavailable
    assert refreshed.resolved["manuscript_session"].status == "active"
    assert refreshed.resolved["draft"].status == "editing"
    assert await e.s.mongo.db[CONTEXTS_COLLECTION].find({}).to_list(None) == before
