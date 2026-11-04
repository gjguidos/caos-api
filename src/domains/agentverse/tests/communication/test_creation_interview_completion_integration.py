"""COORD-0.3 through the production V2 dispatcher on disposable Mongo and Redis.

Explicit confirmations, Manuscript completion and the governed ``agent.draft.propose``
submission, with the real UTU, CPBX, Manuscript, Catalog, Reasoning, draft and Activity
Context services; only the model is a deterministic fake. The proposal is never decided
here: no draft revision, review, approval, Agent creation or activation follows.

Requires ``OPS2_MONGO_INTEGRATION=1`` and ``UTU_TEST_REDIS_URL`` (``scripts/test-harness``).
"""

from typing import Any

import pytest
import pytest_asyncio

from src.base.operations.v2.admission import sha256_hex
from src.domains.agentverse.activities.models import CONTEXTS_COLLECTION, ActivityUnavailable
from src.domains.agentverse.agents.creation_drafts.proposals import PROPOSALS_COLLECTION
from src.domains.agentverse.dependencies.genesis_local import ENKI, TENANT
from src.domains.agentverse.entities.conversation_v2 import ClientEventV2, ConversationV2
from src.domains.agentverse.manuscripts.sessions import SESSIONS_COLLECTION
from src.domains.agentverse.services import creation_interview_coordinator_v2 as coordinator_module
from src.domains.agentverse.tests.agents.test_creation_drafts_integration import (
    local_app,  # noqa: F401 - fixture re-export
)
from src.domains.agentverse.tests.agents.test_creation_review_integration import (
    draft as reviewable,
)
from src.domains.agentverse.tests.agents.test_draft_lifecycle_integration import approval
from src.domains.agentverse.tests.agents.test_draft_proposal_capability_integration import (
    in_session,
)
from src.domains.agentverse.tests.communication.test_creation_interview_answer_integration import (
    ANSWERING,
    answer_event,
    answering,
    candidate,
)
from src.domains.agentverse.tests.communication.test_creation_interview_coordinator_integration import (
    DRAFTS,
    invariants,
    send,
    start_event,
)

ACCEPTED = "agent.draft.proposal.accepted"
CLOSED = "activity.context.closed"
INITIAL = {"name": "Contract Summarizer", "type": "chat", "whitelisted_groups": ["legal-team"]}
TEXT = {
    "purpose": "It summarizes legal contracts for the legal team.",
    "name": "Contract Digest",
    "prompt": "Summarize each contract in five bullet points.",
}
MESSAGE = "Here is my answer for this stage."


def respond(task: dict[str, Any]) -> dict[str, Any]:
    """Deterministic interviewee: one supported candidate per offered field, catalog
    fields take the first offered option, optional lists only for Tools."""
    candidates = []
    for field in task["fields"]:
        key, kind = field["key"], field["type"]
        if kind == "text":
            value: Any = TEXT[key]
        elif kind == "enum":
            value = "private"
        elif kind == "catalog_ref":
            value = field["catalog_options"][0]["key"]
        elif kind == "catalog_ref_list" and key == "tools":
            value = [field["catalog_options"][0]["key"]]
        else:
            continue
        candidates.append(candidate(key, value, MESSAGE))
    return {"candidates": candidates, "clarification_fields": []}


@pytest_asyncio.fixture
async def env(local_app):  # noqa: F811
    e = await answering(local_app, ANSWERING)
    e.model.respond = respond
    e.coordinator = next(iter(e.dispatcher._coordinators.values()))
    try:
        async with in_session(e.s, e.client) as session:
            e.session = session
            yield e
    finally:
        await e.client.aclose()


async def new_draft(e, creation=None, key="coord3-draft-000001"):
    response = await e.client.post(
        DRAFTS,
        json={
            "contract": "caos.agent_creation_draft",
            "schema_version": 1,
            "resource_type": "agent",
            "creation": creation or INITIAL,
        },
        headers={"Idempotency-Key": key},
    )
    assert response.status_code in (200, 201), response.text
    return response.json()["draft_id"]


def event(e, event_id, kind, activity, *, message=None, conversation=None):
    return ClientEventV2.model_validate(
        {
            "conversation_id": conversation or e.conversation,
            "event_id": event_id,
            "type": kind,
            "agent_ids": [ENKI],
            "activity": activity,
            **({"message": message} if message else {}),
        }
    )


def confirm(e, event_id, context_id, revision, field, digest=None, **kw):
    activity = {
        "context_id": context_id,
        "expected_revision": revision,
        "field": field,
        "value": True,
        **({"presentation_digest": digest} if digest else {}),
    }
    return event(e, event_id, "activity.confirm", activity, **kw)


def complete(e, event_id, context_id, revision, **kw):
    activity = {"context_id": context_id, "expected_revision": revision}
    return event(e, event_id, "activity.complete", activity, **kw)


def state(response):
    assert response["type"] == "activity.state", response
    return response["result"]


async def interview_to(e, stop, draft_id, *, prefix="c3", conversation=None):
    """Answer stage by stage up to ``stop`` (the stage to stop at). Returns the context,
    the session and the last outcome."""
    started = state(
        await send(e, start_event(e, f"{prefix}-start", draft_id, conversation=conversation))
    )
    context_id = started["context"]["context_id"]
    session_id = started["manuscript"]["session_id"]
    result = started
    step = 0
    optional_answered: set[str] = set()
    while result["manuscript"]["stage"] != stop:
        step += 1
        revision = result["manuscript"]["revision"]
        stage = result["manuscript"]["stage"]
        if (
            result["next_action"] == "confirm"
            and stage == "capabilities"
            and (stage not in optional_answered)
        ):
            # Only the confirmation is required here: choose the optional Tools first.
            optional_answered.add(stage)
            sent = answer_event(
                e, f"{prefix}-{step:02}", context_id, revision, MESSAGE, conversation=conversation
            )
        elif result["next_action"] == "confirm":
            presentation = result["presentation"]
            digest = None if presentation is None else presentation["digest"]
            (field,) = [f["key"] for f in result["prompt"]["fields"] if f.get("confirmation")]
            sent = confirm(
                e, f"{prefix}-{step:02}", context_id, revision, field, digest,
                conversation=conversation,
            )  # fmt: skip
        else:
            sent = answer_event(
                e, f"{prefix}-{step:02}", context_id, revision, MESSAGE, conversation=conversation
            )
        result = state(await send(e, sent))
        assert result["manuscript"]["revision"] == revision + 1, result
    return context_id, session_id, result


async def ready(e, draft_id, **kw):
    """At the review stage with ``review_ready`` confirmed: ready to complete."""
    context_id, session_id, result = await interview_to(e, "review", draft_id, **kw)
    revision = result["manuscript"]["revision"]
    prefix = kw.get("prefix", "c3")
    confirmed = state(
        await send(
            e,
            confirm(
                e,
                f"{prefix}-ready",
                context_id,
                revision,
                "review_ready",
                conversation=kw.get("conversation"),
            ),
        )
    )
    assert confirmed["confirmation"]["status"] == "confirmed", confirmed
    assert confirmed["manuscript"]["ready_to_complete"] and confirmed["next_action"] == "complete"
    return context_id, session_id, revision + 1


async def proposals(e):
    """Every stored proposal, decoded by its own repository."""
    repository = e.av.agent_draft_proposal_repository()
    ids = [d["_id"] for d in await e.db[PROPOSALS_COLLECTION].find({}, {"_id": 1}).to_list(None)]
    return [(await repository.get(TENANT, i)).model_dump(mode="json") for i in sorted(ids)]


async def draft_record(e, draft_id):
    return await e.av.agent_draft_repository().current(TENANT, draft_id)


async def session_doc(e, session_id):
    return await e.manuscripts._repository.get(TENANT, session_id)


async def context_doc(e, context_id):
    return await e.db[CONTEXTS_COLLECTION].find_one({"_id": context_id})


@pytest.mark.asyncio
async def test_a_confirmed_interview_completes_into_one_pending_proposal(env):
    e = env
    draft_id = await new_draft(e)
    before = await invariants(e)
    base = await draft_record(e, draft_id)
    context_id, session_id, revision = await ready(e, draft_id)

    event_ = complete(e, "c3-complete", context_id, revision)
    envelope = await send(e, event_)
    done = state(envelope)
    completion = done["completion"]
    assert completion["status"] == "proposed", completion
    session = await session_doc(e, session_id)
    assert session.status == "completed"
    assert completion["artifact_digest"] == sha256_hex(session.artifact)
    assert completion["proposal"]["base_revision"] == base.revision
    assert completion["proposal"]["status"] == "pending"
    assert done["next_action"] == "decide_proposal"
    assert (done["context"]["status"], done["context"]["closed_reason"]) == (
        "closed",
        "completed",
    )
    assert done["schema_version"] == 3 and set(done["authorizes"].values()) == {False}

    # The proposal names exactly the interview's differing fields and removes nothing.
    (proposal,) = await proposals(e)
    expected = {k: v for k, v in session.artifact["creation"].items() if base.content.get(k) != v}
    assert proposal["changes"] == expected and proposal["remove"] == []
    assert "whitelisted_groups" not in proposal["changes"]
    # Newly selected Tools carry the artifact's (empty) configuration.
    assert proposal["changes"]["tools"] == [
        {"key": key, "config": {}} for key in session.values()["tools"]
    ]
    assert proposal["proposal_id"] == completion["proposal"]["proposal_id"]
    assert (proposal["status"], proposal["base_revision"]) == ("pending", base.revision)
    # Never decided on the human's behalf: the draft is unchanged.
    after = await draft_record(e, draft_id)
    assert (after.revision, after.content) == (base.revision, base.content)
    assert await e.db["coll_logs"].count_documents({"event_type": ACCEPTED}) == 0
    assert await e.db["coll_logs"].count_documents({"event_type": CLOSED}) == 1
    stored = await context_doc(e, context_id)
    assert stored["references"]["proposal"]["id"] == proposal["proposal_id"]

    # Duplicate delivery replays; a new event after completion reports, never repeats.
    assert await send(e, event_) == envelope
    again = state(await send(e, complete(e, "c3-complete-2", context_id, revision)))
    assert again["completion"]["status"] == "proposed"
    assert again["completion"]["proposal"]["proposal_id"] == proposal["proposal_id"]
    assert len(await proposals(e)) == 1
    assert await e.db[SESSIONS_COLLECTION].count_documents({}) == 1
    assert await e.db["coll_logs"].count_documents({"event_type": CLOSED}) == 1
    # No Agent, policy, grant, draft or activation change.
    assert await invariants(e) == before


@pytest.mark.asyncio
async def test_confirmations_are_explicit_current_and_bound_to_the_offered_listing(
    env, monkeypatch
):
    e = env
    draft_id = await new_draft(e)
    started = state(await send(e, start_event(e, "cf-start", draft_id)))
    context_id = started["context"]["context_id"]
    session_id = started["manuscript"]["session_id"]

    # A later stage's confirmation and an unknown field are refused.
    for i, field in enumerate(("review_ready", "capabilities_reviewed", "purpose", "admin")):
        refused = await send(e, confirm(e, f"cf-future-{i}", context_id, 1, field))
        assert (refused["type"], refused["code"]) == (
            "event.error",
            "confirmation.field_not_current",
        ), refused
    # Completing before readiness changes nothing.
    early = state(await send(e, complete(e, "cf-early", context_id, 1)))
    assert early["completion"]["status"] == "not_ready"
    assert (await session_doc(e, session_id)).revision == 1

    # Up to the capabilities stage (another conversation); Reasoning never confirms: the
    # model also "answers" capabilities_reviewed, which is never offered nor applied.
    conversation = await _conversation(e)
    context_id2, session2, result = await interview_to(
        e, "capabilities", draft_id, prefix="cf2", conversation=conversation
    )
    revision = result["manuscript"]["revision"]
    e.model.respond = lambda task: {
        "candidates": [
            *respond(task)["candidates"],
            candidate("capabilities_reviewed", True, MESSAGE),
        ],
        "clarification_fields": [],
    }
    tools = state(
        await send(
            e,
            answer_event(e, "cf2-tools", context_id2, revision, MESSAGE, conversation=conversation),
        )
    )
    e.model.respond = respond
    assert "capabilities_reviewed" not in (await session_doc(e, session2)).values()
    assert "capabilities_reviewed" not in [f["key"] for f in e.model.calls[-1]["task"]["fields"]]
    assert tools["next_action"] == "confirm"
    revision = tools["manuscript"]["revision"]
    presentation = tools["presentation"]
    assert presentation["revision"] == revision and presentation["context_id"] == context_id2
    entry = presentation["fields"][0]["entries"][0]
    assert set(entry) == {"ref", "key", "version", "title", "descriptor_digest"}
    assert entry["ref"] == f"{entry['key']}@{entry['version']}"

    # Stale revision, missing or foreign listing digest: refused without mutation.
    stale = state(
        await send(
            e,
            confirm(
                e,
                "cf2-stale",
                context_id2,
                revision - 1,
                "capabilities_reviewed",
                presentation["digest"],
                conversation=conversation,
            ),
        )
    )
    assert stale["confirmation"]["status"] == "stale"
    missing = state(
        await send(
            e,
            confirm(
                e,
                "cf2-nodigest",
                context_id2,
                revision,
                "capabilities_reviewed",
                conversation=conversation,
            ),
        )
    )
    assert missing["confirmation"]["status"] == "presentation_stale"
    foreign = state(
        await send(
            e,
            confirm(
                e,
                "cf2-foreign",
                context_id2,
                revision,
                "capabilities_reviewed",
                "0" * 64,
                conversation=conversation,
            ),
        )
    )
    assert foreign["confirmation"]["status"] == "presentation_stale"
    assert (await session_doc(e, session2)).revision == revision

    # An incomplete listing is offered as incomplete and acknowledged as such.
    original = e.coordinator._catalog_page

    async def truncated(principal, spec):
        page = await original(principal, spec)
        return {**page, "next_after": "more"}

    monkeypatch.setattr(e.coordinator, "_catalog_page", truncated)
    offered = state(
        await send(
            e,
            confirm(
                e,
                "cf2-offer",
                context_id2,
                revision,
                "capabilities_reviewed",
                "1" * 64,
                conversation=conversation,
            ),
        )
    )
    incomplete = offered["presentation"]
    assert {f["complete"] for f in incomplete["fields"]} == {False}
    assert incomplete["digest"] != presentation["digest"]
    # The complete listing's digest no longer matches what is offered now.
    outdated = state(
        await send(
            e,
            confirm(
                e,
                "cf2-outdated",
                context_id2,
                revision,
                "capabilities_reviewed",
                presentation["digest"],
                conversation=conversation,
            ),
        )
    )
    assert outdated["confirmation"]["status"] == "presentation_stale"
    event_ = confirm(
        e,
        "cf2-ok",
        context_id2,
        revision,
        "capabilities_reviewed",
        incomplete["digest"],
        conversation=conversation,
    )
    confirmed_envelope = await send(e, event_)
    confirmed = state(confirmed_envelope)
    monkeypatch.undo()
    acknowledged = confirmed["confirmation"]
    assert acknowledged["status"] == "confirmed"
    assert acknowledged["presentation_digest"] == incomplete["digest"]
    assert set(acknowledged["catalog_complete"].values()) == {False}
    assert confirmed["manuscript"]["stage"] == "configuration"
    assert (await session_doc(e, session2)).values()["capabilities_reviewed"] is True
    assert await send(e, event_) == confirmed_envelope  # Duplicate delivery replays.
    # A completed stage's confirmation can no longer be sent.
    late = await send(
        e,
        confirm(
            e,
            "cf2-late",
            context_id2,
            revision + 1,
            "capabilities_reviewed",
            conversation=conversation,
        ),
    )
    assert (late["type"], late["code"]) == ("event.error", "confirmation.field_not_current")


async def _conversation(e):
    conversation = await e.conversations.create(
        ConversationV2(tenant_id=TENANT, member_user_ids=(e.session.user_id,), agent_ids=(ENKI,))
    )
    return conversation.id


@pytest.mark.asyncio
async def test_already_current_drafts_and_locked_drafts(env):
    e = env
    # Already current: the human made the draft match the interview before completion.
    draft_id = await new_draft(e)
    context_id, session_id, revision = await ready(e, draft_id)
    values = (await session_doc(e, session_id)).values()
    current = await draft_record(e, draft_id)
    patched = await e.client.patch(
        f"{DRAFTS}/{draft_id}",
        json={
            "contract": "caos.agent_draft_update",
            "schema_version": 1,
            "expected_revision": current.revision,
            "changes": {
                "name": values["name"],
                "type": values["agent_type"],
                "role_name": values["role"],
                "personality_key": values["soul"],
                # A non-empty existing configuration: kept, so still nothing to propose.
                "tools": [{"key": key, "config": {"top_k": 3}} for key in values["tools"]],
                "prompt": values["prompt"],
                "visibility": values["visibility"],
            },
        },
    )
    assert patched.status_code == 200, patched.text
    done = state(await send(e, complete(e, "ac-complete", context_id, revision)))
    assert done["completion"]["status"] == "already_current", done
    assert done["completion"]["proposal"] is None and done["next_action"] == "none"
    assert done["context"]["closed_reason"] == "completed"
    assert await proposals(e) == []
    stored = await context_doc(e, context_id)
    assert stored["references"]["draft"]["observed_revision"] == current.revision + 1

    # Locked after the Manuscript completed: the human reviewed and approved the draft.
    # A governed conflict: no proposal, no rebase, the context stays active.
    conversation = await _conversation(e)
    locked_id = await new_draft(e, reviewable()["creation"], key="coord3-draft-000002")
    context_id, session_id, revision = await ready(
        e, locked_id, prefix="lk", conversation=conversation
    )
    real_submit = coordinator_module.submit_proposal

    async def down(*args, **kwargs):
        raise ActivityUnavailable("Injected outage before submission")

    coordinator_module.submit_proposal = down
    try:
        failed = await send(e, complete(e, "lk-1", context_id, revision, conversation=conversation))
    finally:
        coordinator_module.submit_proposal = real_submit
    assert (
        failed["type"] == "event.error" and (await session_doc(e, session_id)).status == "completed"
    )
    head = await draft_record(e, locked_id)
    path = f"{DRAFTS}/{locked_id}"
    review = await e.client.post(f"{path}/revisions/{head.revision}/review")
    assert review.status_code == 200, review.text
    approved = await e.client.post(
        f"{path}/approval", json=approval(head.revision, review.json()["review_digest"])
    )
    assert approved.status_code == 200, approved.text
    refused = state(
        await send(e, complete(e, "lk-2", context_id, revision, conversation=conversation))
    )
    assert refused["completion"]["status"] == "conflict", refused
    assert refused["completion"]["conflict"]["code"] == "draft.approved"
    assert await proposals(e) == []
    assert (await context_doc(e, context_id))["status"] == "active"


@pytest.mark.asyncio
async def test_completion_recovers_after_each_durable_step(env, monkeypatch):
    e = env
    draft_id = await new_draft(e)
    base = await draft_record(e, draft_id)
    context_id, session_id, revision = await ready(e, draft_id)
    real_submit = coordinator_module.submit_proposal

    # (1) Manuscript completed; the proposal step failed.
    async def down(*args, **kwargs):
        raise ActivityUnavailable("Injected outage before submission")

    monkeypatch.setattr(coordinator_module, "submit_proposal", down)
    failed = await send(e, complete(e, "rc-1", context_id, revision))
    assert failed["type"] == "event.error", failed
    assert (await session_doc(e, session_id)).status == "completed"
    assert await proposals(e) == []

    # (2) The proposal committed; recording it in the context failed.
    async def submitted_then_lost(*args, **kwargs):
        await real_submit(*args, **kwargs)
        raise ActivityUnavailable("Injected outage after submission")

    monkeypatch.setattr(coordinator_module, "submit_proposal", submitted_then_lost)
    lost = await send(e, complete(e, "rc-2", context_id, revision))
    assert lost["type"] == "event.error", lost
    (proposal,) = await proposals(e)
    assert "proposal" not in (await context_doc(e, context_id))["references"]
    monkeypatch.setattr(coordinator_module, "submit_proposal", real_submit)

    # The human edits an unrelated field meanwhile: the earlier proposal is recovered
    # as it is (its base revision), never rebased or duplicated.
    patched = await e.client.patch(
        f"{DRAFTS}/{draft_id}",
        json={
            "contract": "caos.agent_draft_update",
            "schema_version": 1,
            "expected_revision": base.revision,
            "changes": {"whitelisted_groups": ["legal-team", "audit"]},
        },
    )
    assert patched.status_code == 200, patched.text

    # (3) Recorded in the context; closing failed.
    close = e.contexts.close

    async def close_down(*args, **kwargs):
        raise ActivityUnavailable("Injected outage before closure")

    monkeypatch.setattr(e.contexts, "close", close_down)
    unclosed = await send(e, complete(e, "rc-3", context_id, revision))
    assert unclosed["type"] == "event.error", unclosed
    stored = await context_doc(e, context_id)
    assert stored["status"] == "active"
    assert stored["references"]["proposal"]["id"] == proposal["proposal_id"]
    monkeypatch.setattr(e.contexts, "close", close)

    # (4) The retry converges: one session, one proposal at its original base, closed.
    event_ = complete(e, "rc-4", context_id, revision)
    envelope = await send(e, event_)
    done = state(envelope)
    assert done["completion"]["status"] == "proposed", done
    assert done["completion"]["proposal"]["proposal_id"] == proposal["proposal_id"]
    assert done["completion"]["proposal"]["base_revision"] == base.revision
    assert done["context"]["closed_reason"] == "completed"
    assert len(await proposals(e)) == 1 and (await proposals(e))[0] == proposal
    assert await e.db[SESSIONS_COLLECTION].count_documents({}) == 1
    assert await send(e, event_) == envelope


@pytest.mark.asyncio
async def test_lease_loss_and_revocation_before_completion_write_nothing(env, monkeypatch):
    e = env
    draft_id = await new_draft(e)
    context_id, session_id, revision = await ready(e, draft_id)

    async def lost(lease):
        return False

    monkeypatch.setattr(e.coordinator, "_turns", lost)
    denied = await send(e, complete(e, "ll-1", context_id, revision))
    monkeypatch.undo()
    assert denied["type"] == "event.error", denied
    assert (await session_doc(e, session_id)).status == "active"
    assert await proposals(e) == []

    assert await e.authority.revoke(TENANT, session_id=e.session.session_id)
    try:
        revoked = await send(e, complete(e, "ll-2", context_id, revision))
    except Exception as exc:  # noqa: BLE001 - refusal may surface as a permission error
        revoked = {"type": "event.error", "code": type(exc).__name__}
    assert revoked["type"] == "event.error", revoked
    assert (await session_doc(e, session_id)).status == "active"
    assert await proposals(e) == []


@pytest.mark.asyncio
async def test_tool_configuration_is_preserved_or_refused_when_ambiguous(env):
    e = env
    draft_id = await new_draft(e)
    context_id, session_id, revision = await ready(e, draft_id)
    (tool,) = (await session_doc(e, session_id)).values()["tools"]

    async def set_tools(tools):
        current = await draft_record(e, draft_id)
        patched = await e.client.patch(
            f"{DRAFTS}/{draft_id}",
            json={
                "contract": "caos.agent_draft_update",
                "schema_version": 1,
                "expected_revision": current.revision,
                "changes": {"tools": tools},
            },
        )
        assert patched.status_code == 200, patched.text

    # Two existing entries for the selected Tool: which configuration to keep is
    # undecidable, so the completion is a conflict; nothing is proposed or closed.
    await set_tools([{"key": tool, "config": {"top_k": 1}}, {"key": tool, "config": {"top_k": 2}}])
    ambiguous = state(await send(e, complete(e, "tc-1", context_id, revision)))
    assert ambiguous["completion"]["status"] == "conflict", ambiguous
    assert ambiguous["completion"]["conflict"]["code"] == "completion.tool_identity_ambiguous"
    assert await proposals(e) == []
    assert (await context_doc(e, context_id))["status"] == "active"

    # The selected Tool exists with a configuration (kept) beside one the human did not
    # select. The proposal replaces the Tools field; submission removes nothing.
    existing = [{"key": tool, "config": {"top_k": 3}}, {"key": "unselected_tool", "config": {}}]
    await set_tools(existing)
    done = state(await send(e, complete(e, "tc-2", context_id, revision)))
    assert done["completion"]["status"] == "proposed", done
    (proposal,) = await proposals(e)
    assert proposal["changes"]["tools"] == [{"key": tool, "config": {"top_k": 3}}]
    head = await draft_record(e, draft_id)
    assert head.content["tools"] == existing

    # The human's view shows the consequence of accepting the field before deciding.
    url = f"{DRAFTS}/{draft_id}/proposals/{proposal['proposal_id']}"
    view = (await e.client.get(url)).json()
    (tools,) = [row for row in view["comparison"] if row["field"] == "tools"]
    assert tools["base"] == {"state": "present", "value": existing}
    assert tools["items"]["removed"] == [{"key": "unselected_tool", "config": {}}]
    assert tools["items"]["retained"] == [{"entry": tool, "changed": False}]
    assert tools["items"]["added"] == []
    # Only the human's explicit acceptance of the Tools field removes the unselected Tool.
    accepted = await e.client.post(
        f"{url}/decision",
        json={
            "contract": "caos.agent_draft_proposal_decision",
            "schema_version": 1,
            "confirmed": True,
            "decision": "accept",
            "expected_revision": head.revision,
            "fields": ["tools"],
        },
    )
    assert accepted.status_code == 200, accepted.text
    assert (await draft_record(e, draft_id)).content["tools"] == [
        {"key": tool, "config": {"top_k": 3}}
    ]


@pytest.mark.asyncio
async def test_draft_content_is_read_only_within_the_proposal_scope(env):
    from src.base.events.v2.contracts import EventPermissionError
    from src.domains.agentverse.agents.creation_drafts.capability import PROPOSE_SCOPE
    from src.domains.agentverse.agents.creation_drafts.models import (
        AgentDraftRevision,
        DraftNotFound,
    )
    from src.domains.agentverse.manuscripts.capability import SCOPES
    from src.domains.agentverse.ncsc.authorization import SynapticAuthorization
    from src.domains.agentverse.ncsc.models import SynapticGrants
    from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey

    e = env
    reader = e.coordinator._drafts
    authority = e.authority
    draft_id = await new_draft(e)
    principal = await authority.resolve_agent_principal(
        AgentInstanceKey(TENANT, e.session.user_id, ENKI, e.session.session_id)
    )
    scoped = await authority.attenuate(principal, PROPOSE_SCOPE)
    assert (await reader.content(scoped, draft_id)).content == INITIAL

    # Direct misuse: an unscoped principal or another capability's scope is refused.
    for wrong in (principal, await authority.attenuate(principal, SCOPES["status"])):
        with pytest.raises(EventPermissionError):
            await reader.content(wrong, draft_id)

    # Another tenant's, human's or source Agent's draft is indistinguishable from none.
    repository = e.av.agent_draft_repository()
    head = await repository.current(TENANT, draft_id)
    for i, change in enumerate(
        (
            {"tenant_id": "another-tenant"},
            {"owner_subject_id": "someone-else"},
            {"source_agent_id": "00000000-0000-4000-8000-00000000beef"},
        )
    ):
        other = AgentDraftRevision.build(
            **{
                **head.model_dump(exclude={"revision_digest", "content_digest"}),
                "draft_id": f"00000000-0000-4000-8000-0000000c0de{i}",
                **change,
            }
        )
        assert (await repository.commit(other))[1]
        with pytest.raises(DraftNotFound):
            await reader.content(scoped, other.draft_id)

    # Without the agent.draft.propose grant: no scope, no read, no completion.
    current = authority._authorization
    grants = dict(current._grants)
    grants[(TENANT, ENKI)] = SynapticGrants(
        current.grants_for(principal).invoke - {"agent.draft.propose"}
    )
    await authority.install_policy(
        TENANT,
        expected=await authority._store().current_policy(TENANT),
        authorization=SynapticAuthorization(grants=grants, trusted_lines=current._trusted_lines),
    )
    with pytest.raises(EventPermissionError):
        await reader.content(scoped, draft_id)  # The earlier scope no longer holds.
    fresh = await authority.resolve_agent_principal(
        AgentInstanceKey(TENANT, e.session.user_id, ENKI, e.session.session_id)
    )
    with pytest.raises(EventPermissionError):
        await authority.attenuate(fresh, PROPOSE_SCOPE)
