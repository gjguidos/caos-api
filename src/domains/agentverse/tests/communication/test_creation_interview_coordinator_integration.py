"""COORD-0.1 through the production V2 dispatcher on disposable Mongo and Redis.

Real UTU session and policy, CPBX, the scoped Manuscript provider, the Activity Context
service and Seshat; a test Enki created by JOSHUA through Genesis, configured with
Conversation and Manuscript Tool attachments (the live Enki is not touched). Service
level: events are dispatched in-process (``ClientEventDispatcherV2.dispatch``), the
same entry the WebSocket transport calls; the transport itself is not exercised.

Requires ``OPS2_MONGO_INTEGRATION=1`` and ``UTU_TEST_REDIS_URL`` (``scripts/test-harness``).
"""

import asyncio
from types import SimpleNamespace

import pytest
import pytest_asyncio

from src.base.events.v2.contracts import EventPermissionError
from src.base.infrastructure.ai.client_factory import ClientFactory
from src.domains.agentverse.activities.models import (
    CONTEXTS_COLLECTION,
    HEADS_COLLECTION,
    ActivityUnavailable,
)
from src.domains.agentverse.agents.creation_drafts.models import (
    DRAFTS_COLLECTION,
    AgentDraftRevision,
)
from src.domains.agentverse.dependencies.genesis_local import ENKI, JOSHUA, OWNER, TENANT
from src.domains.agentverse.entities.conversation_v2 import ClientEventV2, ConversationV2
from src.domains.agentverse.manuscripts.capability import SCOPES, SIGNALS
from src.domains.agentverse.manuscripts.definitions import AGENT_CREATION_INTERVIEW
from src.domains.agentverse.manuscripts.sessions import SESSIONS_COLLECTION
from src.domains.agentverse.ncsc.authorization import SynapticAuthorization
from src.domains.agentverse.ncsc.models import SynapticGrants
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey
from src.domains.agentverse.services.creation_interview_coordinator_v2 import prompt
from src.domains.agentverse.tests.agents.test_creation_drafts_integration import (
    local_app,  # noqa: F401 - fixture re-export
    snapshot,
)
from src.domains.agentverse.tests.agents.test_creation_review_integration import creator
from src.domains.agentverse.tests.agents.test_draft_proposal_capability_integration import (
    in_session,
)
from src.domains.agentverse.tests.agents.test_genesis_initiation_integration import (
    endpoint,
    payload,
)
from src.domains.agentverse.tests.agents.test_recursive_genesis_integration import ThothModel
from src.domains.agentverse.tools.chat_tool_v2.synaptic import chat_context
from src.domains.agentverse.tools.manuscript_tool import ManuscriptTool

INTERVIEW = "agent.creation_interview@1.0.0"
DRAFTS = f"/api/v2/agentverse/agents/{ENKI}/creation-drafts"
STARTED, CLOSED = "activity.context.started", "activity.context.closed"
UNRELATED = "00000000-0000-4000-8000-00000000dead"
CONFIGURED = [
    {"key": "conversation", "config": {"modes": ["context", "llm"], "activities": [INTERVIEW]}},
    {"key": "manuscript", "config": {"default_manuscript": "agent_creation_interview@1.0.0"}},
]


async def enki_with(s, tools):
    """G1 with a test-only composition: JOSHUA → Genesis creates a sleeping Enki."""
    s.registry.subscribe("chat.context", provider="tool.chat")(chat_context)
    s.model = ThothModel()
    s.app.state.client_factory = ClientFactory({"llm": {"test": s.model}}, {"llm": "test"})
    assert (await s.client.post(f"/api/v2/agentverse/agents/{JOSHUA}/awaken")).status_code == 200
    body = payload()
    body["creation"]["tools"] = tools
    created = await s.client.post(endpoint(), json=body)
    assert created.json()["status"] == "provisioned", created.text
    return s


async def environment(s, tools):
    s = await enki_with(s, tools)
    av = s.av
    client = await creator(s)
    conversations = av.conversation_repository_v2()
    conversation = await conversations.create(
        ConversationV2(tenant_id=TENANT, member_user_ids=(OWNER,), agent_ids=(ENKI,))
    )
    return SimpleNamespace(
        s=s,
        av=av,
        client=client,
        db=s.mongo.db,
        authority=av.utu_authority(),
        dispatcher=av.client_event_dispatcher_v2(),
        contexts=s.app.state.activity_contexts,
        conversations=conversations,
        conversation=conversation.id,
    )


@pytest_asyncio.fixture
async def env(local_app):  # noqa: F811
    e = await environment(local_app, CONFIGURED)
    try:
        async with in_session(e.s, e.client) as session:
            e.session = session
            yield e
    finally:
        await e.client.aclose()


def start_event(e, event_id, draft_id, *, key=INTERVIEW, conversation=None, agent=ENKI):
    return ClientEventV2.model_validate(
        {
            "conversation_id": conversation or e.conversation,
            "event_id": event_id,
            "type": "activity.start",
            "agent_ids": [agent],
            "activity": {"key": key, "references": {"draft": draft_id}},
        }
    )


def close_event(e, event_id, context_id, *, conversation=None):
    return ClientEventV2.model_validate(
        {
            "conversation_id": conversation or e.conversation,
            "event_id": event_id,
            "type": "activity.close",
            "agent_ids": [ENKI],
            "activity": {"context_id": context_id, "reason": "abandoned"},
        }
    )


async def send(e, event):
    return await e.dispatcher.dispatch(e.session, event)


async def draft(e, name="Contract Summarizer", key=None):
    response = await e.client.post(
        DRAFTS,
        json={
            "contract": "caos.agent_creation_draft",
            "schema_version": 1,
            "resource_type": "agent",
            "creation": {"name": name, "type": "chat"},
        },
        headers={"Idempotency-Key": key or f"coord-draft-{name[:6]}-0001"},
    )
    assert response.status_code in (200, 201), response.text
    return response.json()["draft_id"]


async def counts(e):
    logs = e.db["coll_logs"]
    return {
        "contexts": await e.db[CONTEXTS_COLLECTION].count_documents({}),
        "sessions": await e.db[SESSIONS_COLLECTION].count_documents({}),
        "started": await logs.count_documents({"event_type": STARTED}),
        "closed": await logs.count_documents({"event_type": CLOSED}),
    }


async def invariants(e):
    """What COORD-0.1 must never change: Agents, the policy and Enki's grants."""
    principal = await e.authority.resolve_agent_principal(
        AgentInstanceKey(TENANT, OWNER, ENKI, e.session.session_id)
    )
    return {
        "agents": (await snapshot(e.s))["agents"],
        "policy": await e.authority._store().current_policy(TENANT),
        "grants": e.authority.grants_for(principal).invoke,
        "drafts": await e.db[DRAFTS_COLLECTION].find({}, {"_id": 0}).to_list(None),
        "activations": await e.db["coll_logs"].count_documents(
            {"event_type": {"$regex": "activation"}}
        ),
    }


def expected_first_prompt():
    stage = AGENT_CREATION_INTERVIEW.stage(AGENT_CREATION_INTERVIEW.initial_stage)
    return stage, [AGENT_CREATION_INTERVIEW.field(k) for k in stage.collects]


@pytest.mark.asyncio
async def test_start_replay_resume_and_close_through_the_dispatcher(env):
    e = env
    draft_id = await draft(e)
    other = await draft(e, "Other Agent", key="coord-draft-other-01")
    before = await invariants(e)

    # 1-3. One explicit start: one context, one Manuscript session, the first prompt.
    event = start_event(e, "coord-start-0001", draft_id)
    started = await send(e, event)
    assert started["type"] == "activity.state", started
    assert started["correlation_id"] == event.event_id
    result = started["result"]
    assert (result["contract"], result["activity"], result["agent_id"]) == (
        "caos.activity_turn",
        INTERVIEW,
        ENKI,
    )
    context = result["context"]
    assert (context["status"], context["generation"], context["draft_id"]) == (
        "active",
        1,
        draft_id,
    )
    assert result["resumed"] is False and result["next_action"] == "answer"
    assert result["authorizes"] == {
        "draft_write": False,
        "approval": False,
        "creation": False,
        "activation": False,
    }
    manuscript = result["manuscript"]
    assert (manuscript["manuscript"], manuscript["status"], manuscript["revision"]) == (
        "agent_creation_interview@1.0.0",
        "active",
        1,
    )
    stage, fields = expected_first_prompt()
    assert result["prompt"]["stage"] == stage.key == manuscript["stage"]
    assert [f["key"] for f in result["prompt"]["fields"]] == [f.key for f in fields]
    assert result["prompt"]["fields"][0] == {
        "key": "purpose",
        "title": "Purpose",
        "type": "text",
        "required": True,
    }
    # The deterministic projection of the Manuscript's own view, read independently.
    principal = await e.authority.resolve_agent_principal(
        AgentInstanceKey(TENANT, OWNER, ENKI, e.session.session_id)
    )
    status = await ManuscriptTool().invoke(
        authority=e.authority,
        exchange=e.av.synaptic_exchange(),
        principal=principal,
        op="status",
        command={
            "contract": "caos.manuscript_session.status",
            "schema_version": 1,
            "session_id": manuscript["session_id"],
        },
    )
    assert result["prompt"] == prompt(status["session"], AGENT_CREATION_INTERVIEW)
    assert started["definitions"][0]["agent_id"] == ENKI
    stored = await e.db[CONTEXTS_COLLECTION].find_one({"_id": context["context_id"]})
    assert stored["references"]["manuscript_session"]["id"] == manuscript["session_id"]
    assert stored["created_by"]["event_id"] == event.event_id
    assert await counts(e) == {"contexts": 1, "sessions": 1, "started": 1, "closed": 0}

    # 4. Replay of the same event: the journaled outcome, nothing re-executed.
    assert await send(e, event) == started
    conflict = await send(e, start_event(e, "coord-start-0001", "another-draft"))
    assert (conflict["type"], conflict["code"]) == ("event.error", "conflict")
    # A new event for the same draft resumes the same context and session.
    resumed = await send(e, start_event(e, "coord-start-0002", draft_id))
    assert resumed["result"]["resumed"] is True
    assert resumed["result"]["context"] == context
    assert resumed["result"]["manuscript"] == manuscript
    # Another draft while one is active is refused; nothing is created.
    refused = await send(e, start_event(e, "coord-start-0003", other))
    assert (refused["type"], refused["code"]) == ("event.error", "activity.active_exists")
    assert await counts(e) == {"contexts": 1, "sessions": 1, "started": 1, "closed": 0}

    # 7. An unrelated context ID is indistinguishable from none.
    missing = await send(e, close_event(e, "coord-close-0000", UNRELATED))
    assert (missing["type"], missing["code"]) == ("event.error", "activity.not_found")

    # 6. Explicit close: closed state and its Seshat evidence; replay-safe.
    close = close_event(e, "coord-close-0001", context["context_id"])
    closed = await send(e, close)
    assert closed["type"] == "activity.state", closed
    assert (
        closed["result"]["context"]["status"],
        closed["result"]["context"]["closed_reason"],
    ) == (
        "closed",
        "abandoned",
    )
    assert closed["result"]["next_action"] == "none" and closed["result"]["resumed"] is False
    assert await send(e, close) == closed
    again = await send(e, close_event(e, "coord-close-0002", context["context_id"]))
    assert again["result"]["context"] == closed["result"]["context"]
    assert again["result"]["resumed"] is True
    assert await counts(e) == {"contexts": 1, "sessions": 1, "started": 1, "closed": 1}
    assert await e.contexts.active(await _actor(e), _owner(e)) is None

    # A later start is a new generation with a new interview.
    later = await send(e, start_event(e, "coord-start-0004", draft_id))
    assert later["result"]["context"]["generation"] == 2
    assert later["result"]["manuscript"]["session_id"] != manuscript["session_id"]
    assert await counts(e) == {"contexts": 2, "sessions": 2, "started": 2, "closed": 1}

    # Evidence carries identifiers, never draft content.
    logs = await e.db["coll_logs"].find({"event_type": {"$in": [STARTED, CLOSED]}}).to_list(None)
    assert "Contract Summarizer" not in str(logs)
    # 12. No Agent, grant, policy, draft or activation change.
    assert await invariants(e) == before


async def _actor(e):
    from src.domains.agentverse.activities.service import ActivityActor

    principal = await e.authority.resolve_agent_principal(
        AgentInstanceKey(TENANT, OWNER, ENKI, e.session.session_id)
    )
    return await ActivityActor.from_principal(e.authority, principal)


def _owner(e, conversation=None):
    from src.domains.agentverse.activities.models import OwnerRef

    return OwnerRef(kind="conversation.v2", id=conversation or e.conversation)


@pytest.mark.asyncio
async def test_interruptions_converge_without_duplicates(env, monkeypatch):
    e = env
    draft_id = await draft(e)

    # (a) The Manuscript session started, the context did not: a new event converges.
    original = e.contexts.start

    async def unavailable(*args, **kwargs):
        raise ActivityUnavailable("Injected outage")

    monkeypatch.setattr(e.contexts, "start", unavailable)
    failed = await send(e, start_event(e, "coord-int-0001", draft_id))
    assert (failed["type"], failed["code"], failed["retryable"]) == (
        "event.error",
        "activity.unavailable",
        True,
    )
    assert await counts(e) == {"contexts": 0, "sessions": 1, "started": 0, "closed": 0}
    monkeypatch.setattr(e.contexts, "start", original)
    recovered = await send(e, start_event(e, "coord-int-0002", draft_id))
    assert recovered["type"] == "activity.state" and recovered["result"]["resumed"] is False
    assert await counts(e) == {"contexts": 1, "sessions": 1, "started": 1, "closed": 0}
    first = recovered["result"]["context"]["context_id"]

    # (b) Activation decided (head intent) but the context document was not written:
    # the next start reconciles the decided generation and resumes it.
    closed = await send(e, close_event(e, "coord-int-0003", first))
    assert closed["result"]["context"]["status"] == "closed"
    repository = e.contexts._repository
    insert = repository.insert
    calls = []

    async def lost_insert(context):
        calls.append(context.context_id)
        raise ActivityUnavailable("Injected outage after the head decision")

    monkeypatch.setattr(repository, "insert", lost_insert)
    lost = await send(e, start_event(e, "coord-int-0004", draft_id))
    assert (lost["type"], lost["code"]) == ("event.error", "activity.unavailable"), lost
    assert calls and await e.db[HEADS_COLLECTION].count_documents({"intent": {"$ne": None}}) == 1
    monkeypatch.setattr(repository, "insert", insert)
    settled = await send(e, start_event(e, "coord-int-0005", draft_id))
    assert settled["result"]["resumed"] is True
    assert settled["result"]["context"]["context_id"] == calls[0]
    assert settled["result"]["context"]["generation"] == 2
    assert await counts(e) == {"contexts": 2, "sessions": 2, "started": 2, "closed": 1}

    # (c) Durable state committed, the journal never finished (lease expired): the same
    # event replays ``interrupted``; a new event resumes, it does not duplicate.
    await send(e, close_event(e, "coord-int-0006", calls[0]))
    event = start_event(e, "coord-int-0007", draft_id)
    monkeypatch.setattr(e.conversations, "lease_ms", 2000)
    status, lease = await e.conversations.begin(TENANT, OWNER, event)
    assert status == "started"
    orphan = await e.dispatcher._activity(e.session, event, lease)  # no finish()
    monkeypatch.undo()
    await asyncio.sleep(2.5)
    interrupted = await send(e, event)
    assert (interrupted["code"], interrupted["outcome"]) == ("interrupted", "unknown")
    resumed = await send(e, start_event(e, "coord-int-0008", draft_id))
    assert resumed["result"]["resumed"] is True
    assert resumed["result"]["context"] == orphan["result"]["context"]
    assert await counts(e) == {"contexts": 3, "sessions": 3, "started": 3, "closed": 2}


@pytest.mark.asyncio
async def test_foreign_and_invalid_references_are_refused(env):
    e = env
    draft_id = await draft(e)
    before = await counts(e)

    # 8. Another human's draft (same tenant and source Agent) is indistinguishable from none.
    repository = e.av.agent_draft_repository()
    head = await repository.current(TENANT, draft_id)
    foreign = AgentDraftRevision.build(
        **{
            **head.model_dump(exclude={"revision_digest", "content_digest"}),
            "draft_id": "00000000-0000-4000-8000-0000000f0e19",
            "owner_subject_id": "someone-else",
        }
    )
    assert (await repository.commit(foreign))[1]
    for reference in (foreign.draft_id, UNRELATED):
        refused = await send(e, start_event(e, f"coord-ref-{reference[-4:]}", reference))
        assert (refused["type"], refused["code"]) == ("event.error", "session.draft_unavailable")
    # An unknown activity contract and a non-participant Agent.
    unknown = await send(e, start_event(e, "coord-ref-0003", draft_id, key="agent.other@1.0.0"))
    assert (unknown["type"], unknown["code"]) == ("event.error", "activity.contract_unavailable")
    with pytest.raises(EventPermissionError):
        await send(e, start_event(e, "coord-ref-0004", draft_id, agent=JOSHUA))
    assert await counts(e) == before

    # 7. Another conversation's context cannot be closed from this conversation.
    second = await e.conversations.create(
        ConversationV2(tenant_id=TENANT, member_user_ids=(OWNER,), agent_ids=(ENKI,))
    )
    there = await send(e, start_event(e, "coord-ref-0005", draft_id, conversation=second.id))
    assert there["type"] == "activity.state", there
    context_id = there["result"]["context"]["context_id"]
    here = await send(e, close_event(e, "coord-ref-0006", context_id))
    assert (here["type"], here["code"]) == ("event.error", "activity.not_found")
    stored = await e.db[CONTEXTS_COLLECTION].find_one({"_id": context_id})
    assert stored["status"] == "active"


@pytest.mark.asyncio
async def test_missing_grant_or_scope_fails_closed(env):
    e = env
    draft_id = await draft(e)
    before = await counts(e)
    authority = e.authority
    current = authority._authorization
    grants = dict(current._grants)
    grants[(TENANT, ENKI)] = SynapticGrants(
        current.grants_for(SimpleNamespace(tenant_id=TENANT, agent_id=ENKI)).invoke
        - {SIGNALS["start"]}
    )
    # Disposable tenant only: this run owns its UTU policy key.
    await authority.install_policy(
        TENANT,
        expected=await authority._store().current_policy(TENANT),
        authorization=SynapticAuthorization(grants=grants, trusted_lines=current._trusted_lines),
    )
    denied = await send(e, start_event(e, "coord-grant-0001", draft_id))
    assert (denied["type"], denied["code"]) == ("event.error", "denied")
    assert await counts(e) == before
    # The provider never accepts an unattenuated principal, even with the grant.
    principal = await authority.resolve_agent_principal(
        AgentInstanceKey(TENANT, OWNER, ENKI, e.session.session_id)
    )
    client = e.av.synaptic_exchange().bind(principal)
    try:
        with pytest.raises(EventPermissionError):
            await client.request(SIGNALS["status"], {}, target="system.manuscript", version="1.0.0")
    finally:
        client.revoke()
        await client.drain()
    assert SCOPES["status"].operation == SIGNALS["status"]


@pytest.mark.asyncio
async def test_chat_message_is_unchanged(env):
    e = env
    event = ClientEventV2(
        conversation_id=e.conversation,
        event_id="coord-chat-0001",
        type="chat.message",
        agent_ids=(ENKI,),
        message="hello",
    )
    response = await send(e, event)
    assert response["type"] == "chat.context", response
    assert await send(e, event) == response
    assert await counts(e) == {"contexts": 0, "sessions": 0, "started": 0, "closed": 0}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tools", "code"),
    [
        # The live shape: legacy compatibility declares the activity, no Manuscript Tool.
        ([], "activity.manuscript_unconfigured"),
        # A Conversation Tool that does not declare the activity.
        (
            [{"key": "conversation", "config": {"activities": []}}, CONFIGURED[1]],
            "composition_unavailable",
        ),
        # The activity declared, but the Manuscript Tool names no creation Manuscript.
        (
            [CONFIGURED[0], {"key": "manuscript", "config": {"default_manuscript": "x@1.0.0"}}],
            "activity.manuscript_unconfigured",
        ),
    ],
)
async def test_undeclared_or_unconfigured_compositions_are_refused(local_app, tools, code):  # noqa: F811
    e = await environment(local_app, tools)
    try:
        async with in_session(e.s, e.client) as session:
            e.session = session
            draft_id = await draft(e)
            refused = await send(e, start_event(e, "coord-comp-0001", draft_id))
            assert (refused["type"], refused["code"]) == ("event.error", code), refused
            assert await counts(e) == {"contexts": 0, "sessions": 0, "started": 0, "closed": 0}
    finally:
        await e.client.aclose()
