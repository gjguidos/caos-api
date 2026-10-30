"""MANUSCRIPT-0.1 deterministic runtime: pinned sessions, typed answers, non-linear and
corrective progression, restart, completion, retry safety, concurrency, isolation,
broken or stale state, and the absence of any draft, creation or LLM effect.

Storage is the in-memory MongoDB-like double (real Mongo:
test_manuscript_integration.py); the catalog is a requester-scoped fake, with the real
adapter covered against the validator's catalog fixtures.
"""

import asyncio
import copy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from pymongo.errors import AutoReconnect

from src.base.entities.user import User
from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.agents.creation_drafts.models import AgentDraftCreateRequest
from src.domains.agentverse.manuscripts.catalog import RegistryManuscriptCatalog
from src.domains.agentverse.manuscripts.definitions import register_builtin
from src.domains.agentverse.manuscripts.definitions.agent_creation_interview import (
    AGENT_CREATION_INTERVIEW,
)
from src.domains.agentverse.manuscripts.models import ManuscriptDefinition
from src.domains.agentverse.manuscripts.registry import ManuscriptRegistry
from src.domains.agentverse.manuscripts.repository import MongoManuscriptSessionRepository
from src.domains.agentverse.manuscripts.service import ManuscriptActor, ManuscriptService
from src.domains.agentverse.manuscripts.sessions import (
    SESSIONS_COLLECTION,
    ManuscriptNotAvailable,
    SessionClosed,
    SessionCorrupt,
    SessionIdempotencyConflict,
    SessionIncomplete,
    SessionInvalidRequest,
    SessionNotFound,
    SessionRevisionConflict,
    SessionUnavailable,
)
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey
from src.domains.agentverse.security.revocation import ExecutionPermit
from src.domains.agentverse.tests.agents.test_creation_drafts import DraftMongo
from src.domains.agentverse.tools.validation_tool.models import AgentCreationInput

T0 = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)
OWNER = User(id="owner", tenant_id="tenant", roles=["agentverse-user"])
ACTOR = ManuscriptActor("tenant", "owner", "enki", "agent", OWNER)
KEY = "manuscript-key-000001"
MANUSCRIPT = "agent_creation_interview@1.0.0"


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


class FakeCatalog:
    """Requester-scoped: each subject sees only its own entries; spelling normalized."""

    def __init__(self) -> None:
        self.visible = {
            "owner": {
                ("agent", "chat"),
                ("role", "general"),
                ("role", "writer"),
                ("personality", "empty_eva"),
                ("tool", "search"),
                ("workflow", "flow"),
                ("rulepack", "safe"),
            },
            "someone": {("role", "secret_role")},
        }
        self.calls: list[tuple[str, str, str]] = []

    async def resolve(self, kind: str, value: str, requester: User) -> str | None:
        self.calls.append((kind, value, requester.id))
        key = value.strip().lower()
        return key if (kind, key) in self.visible.get(requester.id, set()) else None


@pytest.fixture
def runtime():
    store = DraftMongo()
    registry = ManuscriptRegistry("manuscripts-runtime")
    register_builtin(registry)
    registry.freeze()
    catalog = FakeCatalog()
    service = ManuscriptService(
        registry=registry,
        repository=MongoManuscriptSessionRepository(store),
        catalog=catalog,
        clock=Clock(),
    )
    return SimpleNamespace(store=store, registry=registry, catalog=catalog, service=service)


def docs(s) -> dict[str, dict[str, Any]]:
    return s.store.documents(SESSIONS_COLLECTION)


counter = iter(range(10**6))


def rk() -> str:
    return f"request-key-{next(counter):010d}"


async def started(s, **kwargs):
    session, _ = await s.service.start(ACTOR, MANUSCRIPT, idempotency_key=KEY, **kwargs)
    return session


async def say(s, session, answers, actor=ACTOR):
    return await s.service.answer(
        actor,
        session.session_id,
        expected_revision=session.revision,
        answers=answers,
        request_key=rk(),
    )


FULL = [
    {"purpose": "Summarize legal contracts for the sales team."},
    {"name": "Contract Summarizer"},
    {"agent_type": "Chat", "role": "general", "soul": "empty_eva"},
    {
        "tools": ["search"],
        "workflows": ["flow"],
        "rule_packs": ["safe"],
        "capabilities_reviewed": True,
    },
    {"prompt": "Summarize contracts faithfully and flag risks.", "visibility": "private"},
    {"review_ready": True},
]


async def interview(s, steps=FULL):
    session = await started(s)
    for answers in steps:
        session, outcome = await say(s, session, answers)
        assert outcome.status == "accepted", (answers, outcome)
    return session


# --- Happy path, determinism and artifact compatibility ------------------------------------------


@pytest.mark.asyncio
async def test_a_complete_interview_produces_the_draft_contract_artifact(runtime):
    s = runtime
    session = await interview(s)
    view = s.service.view(session, AGENT_CREATION_INTERVIEW)
    assert (view["stage"], view["ready_to_complete"], view["missing"]) == ("review", True, [])
    assert view["completed_stages"] == [stage.key for stage in AGENT_CREATION_INTERVIEW.stages]
    done = await s.service.complete(
        ACTOR, session.session_id, expected_revision=session.revision, request_key=rk()
    )
    assert done.status == "completed"
    assert done.artifact == {
        "contract": "caos.agent_creation_draft",
        "schema_version": 1,
        "resource_type": "agent",
        "creation": {
            "name": "Contract Summarizer",
            "type": "chat",
            "role_name": "general",
            "personality_key": "empty_eva",
            "tools": [{"key": "search", "config": {}}],
            "workflows": ["flow"],
            "rule_pack_ids": ["safe"],
            "prompt": "Summarize contracts faithfully and flag risks.",
            "visibility": "private",
        },
    }
    # Structurally the existing draft contract and a full Agent Creation Input.
    AgentDraftCreateRequest.model_validate(done.artifact)
    AgentCreationInput.model_validate(done.artifact["creation"])
    assert s.service.view(done, AGENT_CREATION_INTERVIEW)["authorizes"] == dict.fromkeys(
        ("draft_write", "approval", "creation", "activation"), False
    )
    assert done.manuscript.fingerprint == AGENT_CREATION_INTERVIEW.fingerprint()
    assert {a.source for a in done.answers.values()} == {"agent"}


@pytest.mark.asyncio
async def test_the_same_inputs_always_produce_the_same_progress_and_artifact(runtime):
    s = runtime
    first = await interview(s)
    other = ManuscriptActor("tenant", "owner", "enki-twin", "agent", OWNER)
    session, _ = await s.service.start(other, MANUSCRIPT, idempotency_key=KEY)
    for answers in FULL:
        session, _ = await say(s, session, answers, actor=other)
    assert (session.stage, session.completed_stages, session.values()) == (
        first.stage,
        first.completed_stages,
        first.values(),
    )
    assert s.service.artifact(AGENT_CREATION_INTERVIEW, first.values()) == s.service.artifact(
        AGENT_CREATION_INTERVIEW, session.values()
    )


# --- Partial, non-linear, invalid and corrective answers ------------------------------------------


@pytest.mark.asyncio
async def test_partial_and_volunteered_answers_advance_only_when_stages_are_satisfied(runtime):
    s = runtime
    session = await started(s)
    session, outcome = await say(
        s, session, {"name": "Early Name", "prompt": "Volunteered instructions."}
    )
    assert outcome.status == "needs_clarification" and outcome.missing == ["purpose"]
    assert session.stage == "purpose" and session.values()["name"] == "Early Name"
    session, outcome = await say(s, session, {"purpose": "Answer support questions politely."})
    # Identity was already satisfied: progression passes it and stops at composition.
    assert session.stage == "composition" and session.completed_stages == ["purpose", "identity"]
    assert outcome.missing == ["agent_type", "role", "soul"]


@pytest.mark.asyncio
async def test_invalid_answers_are_rejected_with_codes_and_spend_no_revision(runtime):
    s = runtime
    session = await started(s)
    session, _ = await say(s, session, FULL[0])
    before = session.revision
    same, outcome = await say(
        s,
        session,
        {
            "name": "!!bad name!!",
            "role": "secret_role",  # Another requester's catalog entry.
            "soul": 7,
            "visibility": "everyone",
            "unknown": "x",
            "tools": ["search", "search"],
            "review_ready": "yes",
        },
    )
    assert outcome.status == "invalid" and same.revision == before
    assert outcome.rejected == {
        "name": "answer.invalid",
        "role": "reference.unavailable",
        "soul": "answer.type",
        "visibility": "answer.option",
        "unknown": "field.unknown",
        "tools": "answer.duplicate",
        "review_ready": "answer.type",
    }
    session, outcome = await say(s, same, {"name": "Good Name", "role": "invented_role"})
    assert outcome.accepted == ["name"] and outcome.rejected == {"role": "reference.unavailable"}
    assert ("role", "invented_role", "owner") in s.catalog.calls  # Requester-scoped.


@pytest.mark.asyncio
async def test_completed_stages_change_only_through_correction(runtime):
    s = runtime
    session = await interview(s, FULL[:4])
    assert session.stage == "configuration"
    _, outcome = await say(s, session, {"role": "writer"})
    assert outcome.rejected == {"role": "stage.completed"}
    corrected, outcome = await s.service.correct(
        ACTOR,
        session.session_id,
        expected_revision=session.revision,
        stage="composition",
        answers={"role": "writer"},
        request_key=rk(),
    )
    # Composition and every stage after it reopened; still satisfied, so re-advanced.
    assert corrected.values()["role"] == "writer"
    assert corrected.stage == "configuration"
    assert corrected.completed_stages == ["purpose", "identity", "composition", "capabilities"]
    blanked, outcome = await s.service.correct(
        ACTOR,
        session.session_id,
        expected_revision=corrected.revision,
        stage="composition",
        answers={"soul": None},
        request_key=rk(),
    )
    assert blanked.stage == "composition" and outcome.missing == ["soul"]
    assert blanked.completed_stages == ["purpose", "identity"]
    with pytest.raises(SessionInvalidRequest):
        await s.service.correct(
            ACTOR,
            session.session_id,
            expected_revision=blanked.revision,
            stage="review",
            request_key=rk(),
        )


@pytest.mark.asyncio
async def test_completion_requires_every_typed_requirement(runtime):
    s = runtime
    session = await interview(s, FULL[:5])
    with pytest.raises(SessionIncomplete) as raised:
        await s.service.complete(
            ACTOR, session.session_id, expected_revision=session.revision, request_key=rk()
        )
    assert raised.value.missing == ["review_ready"]
    session, outcome = await say(s, session, {"review_ready": False})
    assert outcome.status == "needs_clarification" and session.stage == "review"
    with pytest.raises(SessionIncomplete):
        await s.service.complete(
            ACTOR, session.session_id, expected_revision=session.revision, request_key=rk()
        )


# --- Retry safety, concurrency and recovery --------------------------------------------------------


@pytest.mark.asyncio
async def test_requests_are_retry_safe_and_keys_cannot_be_reused(runtime):
    s = runtime
    session = await started(s)
    key = rk()
    first, _ = await s.service.answer(
        ACTOR, session.session_id, expected_revision=1, answers=FULL[0], request_key=key
    )
    again, _ = await s.service.answer(
        ACTOR, session.session_id, expected_revision=1, answers=FULL[0], request_key=key
    )
    assert again == first and first.revision == 2
    with pytest.raises(SessionIdempotencyConflict):
        await s.service.answer(
            ACTOR,
            session.session_id,
            expected_revision=1,
            answers={"purpose": "Other purpose text."},
            request_key=key,
        )
    with pytest.raises(SessionRevisionConflict) as raised:
        await s.service.answer(
            ACTOR, session.session_id, expected_revision=1, answers=FULL[1], request_key=rk()
        )
    assert raised.value.current_revision == 2


@pytest.mark.asyncio
async def test_a_write_lost_after_commit_is_replayed_not_reapplied(runtime):
    s = runtime
    session = await started(s)
    collection = s.store.collections[SESSIONS_COLLECTION]
    original = collection.replace_one

    async def commit_then_fail(query, document):
        await original(query, document)
        raise AutoReconnect("connection lost after commit")

    collection.replace_one = commit_then_fail
    key = rk()
    with pytest.raises(SessionUnavailable):
        await s.service.answer(
            ACTOR, session.session_id, expected_revision=1, answers=FULL[0], request_key=key
        )
    collection.replace_one = original
    replay, _ = await s.service.answer(
        ACTOR, session.session_id, expected_revision=1, answers=FULL[0], request_key=key
    )
    assert replay.revision == 2 and replay.stage == "identity"


@pytest.mark.asyncio
async def test_concurrent_transitions_from_one_revision_apply_exactly_once(runtime):
    s = runtime
    session = await started(s)
    results = await asyncio.gather(
        *[
            s.service.answer(
                ACTOR,
                session.session_id,
                expected_revision=1,
                answers={"purpose": f"Concurrent purpose number {n}."},
                request_key=rk(),
            )
            for n in range(8)
        ],
        return_exceptions=True,
    )
    winners = [r for r in results if not isinstance(r, BaseException)]
    assert len(winners) == 1
    assert all(
        isinstance(r, SessionRevisionConflict) for r in results if isinstance(r, BaseException)
    )
    stored = await s.service.status(ACTOR, session.session_id)
    assert (
        stored["revision"] == 2
        and stored["answers"]["purpose"] == winners[0][0].values()["purpose"]
    )


# --- Restart, closed sessions and isolation ----------------------------------------------------------


@pytest.mark.asyncio
async def test_restart_starts_over_and_keeps_the_previous_session_read_only(runtime):
    s = runtime
    session = await interview(s)
    done = await s.service.complete(
        ACTOR, session.session_id, expected_revision=session.revision, request_key=rk()
    )
    fresh = await s.service.restart(ACTOR, done.session_id, request_key=rk())
    assert (fresh.revision, fresh.stage, fresh.answers, fresh.restarted_from) == (
        1,
        "purpose",
        {},
        done.session_id,
    )
    assert fresh.manuscript == done.manuscript
    assert await s.service.restart(ACTOR, done.session_id, request_key=rk()) == fresh  # Replay.
    old = await s.service.status(ACTOR, done.session_id)
    assert old["status"] == "restarted" and old["artifact"] == done.artifact
    with pytest.raises(SessionClosed) as closed:
        await s.service.answer(
            ACTOR,
            done.session_id,
            expected_revision=old["revision"],
            answers=FULL[0],
            request_key=rk(),
        )
    assert closed.value.code == "session.restarted"


@pytest.mark.asyncio
async def test_a_restart_interrupted_after_the_successor_is_completed_by_repeating_it(runtime):
    s = runtime
    session = await started(s)
    collection = s.store.collections[SESSIONS_COLLECTION]
    collection.replace_error = AutoReconnect("crash before closing the predecessor")
    with pytest.raises(SessionUnavailable):
        await s.service.restart(ACTOR, session.session_id, request_key=rk())
    assert len(docs(s)) == 2  # Successor exists; predecessor still active.
    fresh = await s.service.restart(ACTOR, session.session_id, request_key=rk())
    assert (await s.service.status(ACTOR, session.session_id))["restarted_to"] == fresh.session_id
    assert len(docs(s)) == 2


@pytest.mark.asyncio
async def test_completed_sessions_are_read_only(runtime):
    s = runtime
    session = await interview(s)
    done = await s.service.complete(
        ACTOR, session.session_id, expected_revision=session.revision, request_key=rk()
    )
    for call in (
        lambda: say(s, done, {"name": "Changed"}),
        lambda: s.service.correct(
            ACTOR,
            done.session_id,
            expected_revision=done.revision,
            stage="identity",
            request_key=rk(),
        ),
        lambda: s.service.complete(
            ACTOR, done.session_id, expected_revision=done.revision, request_key=rk()
        ),
    ):
        with pytest.raises(SessionClosed) as closed:
            await call()
        assert closed.value.code == "session.completed"


@pytest.mark.asyncio
async def test_sessions_are_isolated_by_tenant_owner_and_source_agent(runtime):
    s = runtime
    session = await started(s)
    for actor in (
        ManuscriptActor("other-tenant", "owner", "enki", "agent", OWNER),
        ManuscriptActor(
            "tenant", "intruder", "enki", "agent", User(id="intruder", tenant_id="tenant")
        ),
        ManuscriptActor("tenant", "owner", "thoth", "agent", OWNER),
    ):
        with pytest.raises(SessionNotFound):
            await s.service.status(actor, session.session_id)
        with pytest.raises(SessionNotFound):
            await s.service.answer(
                actor, session.session_id, expected_revision=1, answers=FULL[0], request_key=rk()
            )
    assert (await s.service.status(ACTOR, session.session_id))["revision"] == 1


# --- Start, prefill and pinning ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_is_idempotent_and_prefill_is_validated_from_draft_content(runtime):
    s = runtime
    prefill = s.service.prefill_from_creation(
        AGENT_CREATION_INTERVIEW,
        {
            "name": "From Draft",
            "type": "chat",
            "role_name": "invented",
            "tools": [{"key": "search", "config": {"x": 1}}],
            "gui_components": [],
        },
    )
    assert prefill == {
        "name": "From Draft",
        "agent_type": "chat",
        "role": "invented",
        "tools": ["search"],
    }
    session, rejected = await s.service.start(
        ACTOR, MANUSCRIPT, idempotency_key=KEY, draft_id="draft-1", prefill=prefill
    )
    assert rejected == {"role": "reference.unavailable"}
    assert {k: a.source for k, a in session.answers.items()} == dict.fromkeys(
        ("name", "agent_type", "tools"), "prefill"
    )
    assert session.stage == "purpose" and session.draft_id == "draft-1"
    replay, _ = await s.service.start(
        ACTOR, MANUSCRIPT, idempotency_key=KEY, draft_id="draft-1", prefill=prefill
    )
    assert replay == session
    with pytest.raises(SessionIdempotencyConflict):
        await s.service.start(ACTOR, MANUSCRIPT, idempotency_key=KEY)
    with pytest.raises(SessionInvalidRequest):
        await s.service.start(
            ACTOR, MANUSCRIPT, idempotency_key="other-key-000000001", prefill={"purpose": "x"}
        )
    with pytest.raises(ManuscriptNotAvailable):
        await s.service.start(
            ACTOR, "agent_creation_interview@9.9.9", idempotency_key="other-key-000000002"
        )


@pytest.mark.asyncio
async def test_sessions_resolve_only_their_exact_pinned_manuscript(runtime):
    s = runtime
    session = await started(s)
    elsewhere = ManuscriptService(
        registry=ManuscriptRegistry("empty-registry"),
        repository=s.service._repository,
        catalog=s.catalog,
    )
    with pytest.raises(ManuscriptNotAvailable):  # Stale: not registered here.
        await elsewhere.status(ACTOR, session.session_id)
    changed = copy.deepcopy(AGENT_CREATION_INTERVIEW.model_dump(mode="json"))
    changed["title"] = "Different content, same version"
    other = ManuscriptRegistry("drifted-registry", published={})
    other.register_definition(ManuscriptDefinition.model_validate(changed))
    drifted = ManuscriptService(registry=other, repository=s.service._repository, catalog=s.catalog)
    with pytest.raises(ManuscriptNotAvailable):  # Same key, different fingerprint.
        await drifted.status(ACTOR, session.session_id)


@pytest.mark.asyncio
async def test_tampered_or_broken_sessions_fail_closed(runtime):
    s = runtime
    session = await started(s)
    stored = docs(s)[session.session_id]
    original = copy.deepcopy(stored)
    stored["answers"] = {"name": {"value": "Injected", "source": "human", "revision": 1}}
    with pytest.raises(SessionCorrupt):
        await s.service.status(ACTOR, session.session_id)
    docs(s)[session.session_id] = original
    broken = session.model_copy(update={"stage": "nonexistent"})
    from src.domains.agentverse.manuscripts.repository import to_document
    from src.domains.agentverse.manuscripts.sessions import ManuscriptSession

    resigned = ManuscriptSession.build(**broken.model_dump(exclude={"state_digest"}))
    docs(s)[session.session_id] = to_document(resigned)
    with pytest.raises(SessionCorrupt):
        await s.service.status(ACTOR, session.session_id)


@pytest.mark.asyncio
async def test_progression_is_bounded_even_for_cyclic_definitions(runtime):
    s = runtime
    data = {
        "contract": "caos.manuscript",
        "schema_version": 1,
        "id": "cycle",
        "version": "1.0.0",
        "title": "Cycle",
        "artifact": {
            "contract": "caos.agent_creation_draft",
            "schema_version": 1,
            "resource_type": "agent",
        },
        "fields": [{"key": "note", "title": "Note", "type": "text"}],
        "stages": [
            {
                "key": "a",
                "title": "A",
                "collects": ["note"],
                "completion": {"op": "always"},
                "transitions": [{"to": "b", "when": {"op": "always"}}],
            },
            {
                "key": "b",
                "title": "B",
                "collects": [],
                "transitions": [
                    {"to": "a", "when": {"op": "always"}},
                    {"to": "c", "when": {"op": "always"}},
                ],
            },
            {"key": "c", "title": "C", "collects": []},
        ],
        "initial_stage": "a",
        "final_stage": "c",
        "completion": {"op": "always"},
    }
    registry = ManuscriptRegistry("cycle-registry", published={})
    registry.register_definition(ManuscriptDefinition.model_validate(data))
    service = ManuscriptService(
        registry=registry, repository=s.service._repository, catalog=s.catalog
    )
    session, _ = await service.start(ACTOR, "cycle@1.0.0", idempotency_key="cycle-key-000000001")
    assert session.stage in ("a", "b") and len(session.completed_stages) <= 2


# --- Authority boundaries ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_only_a_verified_agent_in_a_live_human_session_becomes_an_actor():
    key = AgentInstanceKey("tenant", "owner", "enki", "session")
    authority = SimpleNamespace(
        check_permit=pytest.importorskip("unittest.mock").AsyncMock(),
        resource_requester=pytest.importorskip("unittest.mock").AsyncMock(return_value=OWNER),
    )
    actor = await ManuscriptActor.from_permit(authority, ExecutionPermit(key=key, versions=("p",)))
    assert (actor.tenant_id, actor.subject_id, actor.agent_id, actor.kind) == (
        "tenant",
        "owner",
        "enki",
        "agent",
    )
    with pytest.raises(EventPermissionError):
        await ManuscriptActor.from_permit(
            authority, ExecutionPermit(key=key, versions=("p",), origin="bootstrap")
        )
    authority.check_permit.side_effect = EventPermissionError("revoked")
    with pytest.raises(EventPermissionError):
        await ManuscriptActor.from_permit(authority, ExecutionPermit(key=key, versions=("p",)))


@pytest.mark.asyncio
async def test_the_runtime_touches_only_its_sessions(runtime):
    s = runtime
    session = await interview(s)
    await s.service.complete(
        ACTOR, session.session_id, expected_revision=session.revision, request_key=rk()
    )
    assert set(s.store.collections) == {SESSIONS_COLLECTION}
    assert not any(
        hasattr(s.service, name)
        for name in ("_drafts", "_proposals", "_approvals", "_store", "_exchange", "_model", "_llm")
    )


# --- The real requester-scoped catalog adapter -------------------------------------------------------


@pytest.mark.asyncio
async def test_the_catalog_adapter_reuses_the_validator_read_policy(monkeypatch):
    from src.domains.agentverse.registries.registries import registries
    from src.domains.agentverse.services.workflow_service import WorkflowService
    from src.domains.agentverse.tests.tools.test_data_validation_tool import (
        REQUESTER,
        TENANT,
        FakeWorkflowDB,
        catalog,
        workflow,
    )

    catalogs = catalog()
    for kind, registry in catalogs.items():  # As the validator tests do.
        monkeypatch.setitem(registries, kind, registry)
    adapter = RegistryManuscriptCatalog(
        registries=catalogs,
        workflow_service=WorkflowService(FakeWorkflowDB([workflow("flow", tools=["plain"])])),
    )
    me = User(id=REQUESTER, tenant_id=TENANT)
    assert await adapter.resolve("agent", "Chat", me) == "chat"
    assert await adapter.resolve("role", "general", me) == "general"
    assert await adapter.resolve("tool", "own_private", me) == "own_private"
    assert await adapter.resolve("workflow", "flow", me) == "flow"
    for kind, value in (
        ("role", "foreign_role"),  # Another tenant's.
        ("tool", "others_private"),  # Someone else's private Tool.
        ("tool", "core"),  # Internal system Tool.
        ("personality", "invented"),
        ("workflow", "missing"),
        ("unknown_kind", "x"),
    ):
        assert await adapter.resolve(kind, value, me) is None, (kind, value)
