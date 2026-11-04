"""ENKI-E2E-0.1: governed Agent creation from an Enki interview, end to end.

Human → Enki interview (production V2 dispatcher: ``activity.start``, ``activity.answer``,
``activity.confirm``, ``activity.complete``) → Manuscript completion → one pending
``agent.draft.propose`` proposal → the human's proposal decision (HTTP) → G3.3 review and
human approval (HTTP) → Genesis ``creation-draft/create`` (HTTP) → exactly one sleeping
Agent, with Seshat evidence.

Disposable Mongo and Redis through the harness, the reviewed local UTU policy, and the
production UTU, CPBX, Manuscript, Catalog, Reasoning, Activity Context, draft, proposal,
review, approval and Genesis services. Only the Reasoning model is a deterministic fake;
no authority or governance decision is mocked. The test Enki is created by JOSHUA through
Genesis with Conversation, Manuscript and Reasoning Tool attachments (the live Enki is not
touched).

Requires ``OPS2_MONGO_INTEGRATION=1`` and ``UTU_TEST_REDIS_URL`` (``scripts/test-harness``).
"""

import pytest

from src.base.operations.v2.admission import sha256_hex
from src.domains.agentverse.agents.creation_drafts.proposals import PROPOSALS_COLLECTION
from src.domains.agentverse.dependencies.genesis_local import ENKI, TENANT
from src.domains.agentverse.manuscripts.sessions import SESSIONS_COLLECTION
from src.domains.agentverse.tests.agents.test_creation_review_integration import creator
from src.domains.agentverse.tests.agents.test_draft_lifecycle_integration import approval
from src.domains.agentverse.tests.communication.test_creation_interview_answer_integration import (
    candidate,
)
from src.domains.agentverse.tests.communication.test_creation_interview_completion_integration import (
    DRAFTS,
    MESSAGE,
    TEXT,
    complete,
    draft_record,
    env,  # noqa: F401 - fixture re-export
    local_app,  # noqa: F401 - fixture re-export
    new_draft,
    proposals,
    ready,
    session_doc,
    state,
)
from src.domains.agentverse.tests.communication.test_creation_interview_coordinator_integration import (
    invariants,
    send,
)

CREATE = f"/api/v2/agentverse/agents/{ENKI}/creation-draft/create"
GENESIS = "00000000-0000-4000-8000-000000000003"
INITIAL = {"name": "Contract Summarizer", "type": "chat"}
# What the human chooses from their own catalog: a valid composition (the same Type, Role
# and Soul the G3.3 review tests use) and one optional Tool.
CHOICES = {"agent_type": "chat", "role": "general", "soul": "empty_eva", "tools": ["conversation"]}


def interviewee(task):
    """Deterministic human: text from TEXT, ``private``, and the CHOICES above, each of
    which must be among the options Enki was given from the human's catalog."""
    candidates = []
    for field in task["fields"]:
        key, kind = field["key"], field["type"]
        if kind == "text":
            value = TEXT[key]
        elif kind == "enum":
            value = "private"
        elif key in CHOICES:
            offered = {option["key"] for option in field["catalog_options"]}
            chosen = CHOICES[key]
            assert set(chosen if isinstance(chosen, list) else [chosen]) <= offered, (key, offered)
            value = chosen
        else:
            continue
        candidates.append(candidate(key, value, MESSAGE))
    return {"candidates": candidates, "clarification_fields": []}


def decision(body):
    return {
        "contract": "caos.agent_draft_proposal_decision",
        "schema_version": 1,
        "confirmed": True,
        **body,
    }


def creation(approval_body, key):
    return (
        {
            "contract": "caos.agent_creation_request",
            "schema_version": 1,
            "approval_id": approval_body["approval_id"],
            "expected_review_digest": approval_body["review_digest"],
        },
        {"Idempotency-Key": key},
    )


async def events(e):
    """Seshat evidence by type (identifiers and codes only)."""
    logs = await e.db["coll_logs"].find({}, {"_id": 0}).to_list(None)
    grouped: dict[str, list[dict]] = {}
    for log in logs:
        grouped.setdefault(log["event_type"], []).append(log)
    return grouped


@pytest.mark.asyncio
async def test_an_enki_interview_becomes_exactly_one_sleeping_agent(env):  # noqa: F811
    e = env
    e.model.respond = interviewee
    client = e.client
    stranger = await creator(e.s, roles=("caos.admin",))  # Same human, not a creator.
    try:
        # --- Interview (production V2 dispatcher) ----------------------------------------
        draft_id = await new_draft(e, INITIAL, key="enki-e2e-draft-0001")
        path = f"{DRAFTS}/{draft_id}"
        base = await draft_record(e, draft_id)
        agents_before = await e.db["coll_agents"].count_documents({})
        authority_before = {
            k: v for k, v in (await invariants(e)).items() if k in ("policy", "grants")
        }
        instances_before = dict(e.av.instance_registry_v2()._map)
        context_id, session_id, revision = await ready(e, draft_id, prefix="e2e")
        # Every stage was completed in Manuscript order (answers + explicit confirmations).
        session = await session_doc(e, session_id)
        assert session.completed_stages == [s.key for s in session_definition(e).stages]

        complete_event = complete(e, "e2e-complete", context_id, revision)
        completed_envelope = await send(e, complete_event)
        done = state(completed_envelope)
        assert done["completion"]["status"] == "proposed", done
        session = await session_doc(e, session_id)
        assert session.status == "completed" and session.artifact["creation"]["name"]
        assert done["completion"]["artifact_digest"] == sha256_hex(session.artifact)
        assert (done["context"]["status"], done["context"]["closed_reason"]) == (
            "closed",
            "completed",
        )
        # Duplicate delivery and a retry under a new event: no second anything.
        assert await send(e, complete_event) == completed_envelope
        retry = state(await send(e, complete(e, "e2e-complete-2", context_id, revision)))
        assert retry["completion"]["proposal"] == {
            **done["completion"]["proposal"],
            "status": "pending",
        }

        # --- Proposal --------------------------------------------------------------------
        (proposal,) = await proposals(e)
        assert proposal["status"] == "pending" and proposal["origin"] == "enki"
        assert (proposal["draft_id"], proposal["base_revision"]) == (draft_id, base.revision)
        assert proposal["proposal_id"] == done["completion"]["proposal"]["proposal_id"]
        expected = {
            k: v for k, v in session.artifact["creation"].items() if base.content.get(k) != v
        }
        assert proposal["changes"] == expected and proposal["remove"] == []
        unchanged = await draft_record(e, draft_id)
        assert (unchanged.revision, unchanged.content) == (base.revision, base.content)
        url = f"{path}/proposals/{proposal['proposal_id']}"
        view = (await client.get(url)).json()
        for row in view["comparison"]:
            field = row["field"]
            if field in base.content:
                assert row["base"] == {"state": "present", "value": base.content[field]}
            else:
                assert row["base"] == {"state": "absent"}
            assert row["proposed"] == {"state": "present", "value": expected[field]}
            assert row["conflicting"] is False
        # A decision must name fields and the revision the human saw.
        unselected = await client.post(
            f"{url}/decision", json=decision({"decision": "accept", "fields": []})
        )
        assert unselected.status_code == 422, unselected.text

        # --- Human acceptance (HTTP) -------------------------------------------------------
        stale = await client.post(
            f"{url}/decision",
            json=decision(
                {
                    "decision": "accept",
                    "expected_revision": base.revision + 1,
                    "fields": sorted(expected),
                }
            ),
        )
        assert stale.status_code == 409, stale.text
        foreign = await stranger.post(
            f"{url}/decision",
            json=decision(
                {"decision": "accept", "expected_revision": base.revision, "fields": ["name"]}
            ),
        )
        assert foreign.status_code in (403, 404), foreign.text
        accept = decision(
            {"decision": "accept", "expected_revision": base.revision, "fields": sorted(expected)}
        )
        accepted = await client.post(f"{url}/decision", json=accept)
        assert accepted.status_code == 200, accepted.text
        body = accepted.json()
        assert body["proposal"]["state"] == "accepted"
        accepted_draft = body["draft"]
        assert accepted_draft["revision"] == base.revision + 1
        assert accepted_draft["draft"]["creation"] == {**base.content, **expected}
        # Replay is identical; a different decision is refused; still one revision.
        replay = await client.post(f"{url}/decision", json=accept)
        assert replay.status_code == 200 and replay.json()["draft"] == accepted_draft
        reject = await client.post(f"{url}/decision", json=decision({"decision": "reject"}))
        assert reject.status_code == 409, reject.text
        assert (await draft_record(e, draft_id)).revision == base.revision + 1
        assert len((await events(e)).get("agent.draft.proposal.accepted", [])) == 1

        # --- Genesis review and human approval (HTTP) --------------------------------------
        revision = accepted_draft["revision"]
        review = await client.post(f"{path}/revisions/{revision}/review")
        assert review.status_code == 200, review.text
        envelope = review.json()
        assert envelope["eligibility"]["eligible"], envelope
        assert envelope["draft_ref"]["revision"] == revision
        # No Agent can be created before the human approves.
        missing = await client.post(
            CREATE,
            json={
                "contract": "caos.agent_creation_request",
                "schema_version": 1,
                "approval_id": "00000000-0000-4000-8000-00000000a999",
                "expected_review_digest": envelope["review_digest"],
            },
            headers={"Idempotency-Key": "enki-e2e-create-missing"},
        )
        assert (missing.status_code, missing.json()["detail"]["code"]) == (
            404,
            "approval.not_found",
        ), missing.text
        approved = await client.post(
            f"{path}/approval",
            json=approval(revision, envelope["review_digest"], "enki-e2e-approval-0001"),
        )
        assert approved.status_code == 200, approved.text
        approved_body = approved.json()
        assert approved_body["draft"]["status"] == "approved"

        # --- Governed creation (HTTP) ------------------------------------------------------
        request, headers = creation(approved_body["approval"], "enki-e2e-create-0001")
        created = await client.post(CREATE, json=request, headers=headers)
        assert created.status_code == 200, created.text
        record = created.json()
        assert record["state"] == "completed" and record["authorizes"] == {"activation": False}
        agent_id = record["agent_id"]
        # Duplicate creation (same key) replays; a new key cannot create a second Agent.
        again = await client.post(CREATE, json=request, headers=headers)
        assert again.status_code == 200 and again.json()["agent_id"] == agent_id
        other = await client.post(
            CREATE, json=request, headers={"Idempotency-Key": "enki-e2e-create-0002"}
        )
        assert (other.status_code, other.json()["detail"]["code"]) == (409, "approval.consumed")

        # --- The resulting Agent -----------------------------------------------------------
        assert await e.db["coll_agents"].count_documents({}) == agents_before + 1
        agent = await e.db["coll_agents"].find_one({"id": agent_id}, {"_id": 0})
        final = accepted_draft["draft"]["creation"]
        assert agent["name"] == final["name"] and agent["runtime_version"] == "v2"
        assert agent["role_name"] == final["role_name"]
        assert agent["personality_key"] == final["personality_key"]
        assert [t["key"] for t in agent.get("tools") or []] == [
            t["key"] for t in final.get("tools") or []
        ]
        consumed = (await client.get(path)).json()
        assert consumed["status"] == "consumed" and consumed["lifecycle"]["agent_id"] == agent_id
        # Sleeping: no runtime instance was created or awakened, and nobody may wake it.
        assert dict(e.av.instance_registry_v2()._map) == instances_before
        woken = await client.post(f"/api/v2/agentverse/agents/{agent_id}/awaken")
        assert woken.status_code == 403, woken.text
        # Nothing authorizes lifecycle operations on it yet, not even a status read.
        status = await client.get(f"/api/v2/agentverse/agents/{agent_id}")
        assert status.status_code == 403, status.text
        assert dict(e.av.instance_registry_v2()._map) == instances_before
        # No grant or policy change: the new Agent holds no capability.
        assert {
            k: v for k, v in (await invariants(e)).items() if k in ("policy", "grants")
        } == authority_before
        assert not any(a == agent_id for (_, a) in e.authority._authorization._grants)

        # --- Seshat evidence ---------------------------------------------------------------
        evidence = await events(e)
        for kind, count in {
            "activity.context.started": 1,
            "activity.context.closed": 1,
            "agent.draft.proposal.accepted": 1,
            "agent.creation_review.approved": 1,
            "agent.creation.authorized": 1,
            "agent.creation.completed": 1,
        }.items():
            assert len(evidence.get(kind, [])) == count, (kind, sorted(evidence))
        assert not evidence.get("genesis.activation.completed")
        (completed_event,) = evidence["agent.creation.completed"]
        assert completed_event["tenant_id"] == TENANT
        assert completed_event["actor"] == {"principal_id": GENESIS, "user_id": e.session.user_id}
        assert completed_event["subject"] == {"resource_type": "agent", "resource_id": agent_id}
        assert completed_event["operation_id"] == record["operation_id"]
        for kind in ("agent.draft.proposal.accepted", "agent.creation_review.approved"):
            (log,) = evidence[kind]
            assert (log["tenant_id"], log["actor"]["user_id"]) == (TENANT, e.session.user_id)
        # Evidence carries identifiers, never draft or prompt content.
        assert TEXT["prompt"] not in str(evidence) and TEXT["purpose"] not in str(evidence)
        assert "agent.draft.proposal.rejected" not in evidence

        # --- Revoked authority: nothing more can be created -------------------------------
        assert await e.authority.revoke(TENANT, session_id=e.session.session_id)
        revoked = await client.post(
            CREATE, json=request, headers={"Idempotency-Key": "enki-e2e-create-0003"}
        )
        assert revoked.status_code == 403, revoked.text
        assert await e.db["coll_agents"].count_documents({}) == agents_before + 1
        assert len(await proposals(e)) == 1
        assert await e.db[SESSIONS_COLLECTION].count_documents({}) == 1
        assert await e.db[PROPOSALS_COLLECTION].count_documents({}) == 1
    finally:
        await stranger.aclose()


def session_definition(e):
    from src.domains.agentverse.manuscripts.definitions import AGENT_CREATION_INTERVIEW

    return AGENT_CREATION_INTERVIEW
