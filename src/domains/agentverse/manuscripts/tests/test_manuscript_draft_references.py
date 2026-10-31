"""MANUSCRIPT-0.1C: read-only draft-reference integrity for Manuscript sessions.

The real draft repository and ``DraftReferenceReader`` over the in-memory Mongo double;
draft records are real, digest-chained revisions (approved and consumed included).
"""

import copy
from datetime import UTC, datetime
from typing import Any

import pytest

from src.base.entities.user import User
from src.domains.agentverse.agents.creation_drafts.models import (
    AgentDraftRevision,
    DraftUnavailable,
    FieldProvenance,
    RevisionCause,
)
from src.domains.agentverse.agents.creation_drafts.references import (
    DraftReference,
    DraftReferenceReader,
)
from src.domains.agentverse.agents.creation_drafts.repository import MongoAgentDraftRepository
from src.domains.agentverse.manuscripts.definitions import register_builtin
from src.domains.agentverse.manuscripts.registry import ManuscriptRegistry
from src.domains.agentverse.manuscripts.repository import MongoManuscriptSessionRepository
from src.domains.agentverse.manuscripts.service import ManuscriptActor, ManuscriptService
from src.domains.agentverse.manuscripts.sessions import (
    SESSIONS_COLLECTION,
    DraftNotEditable,
    DraftReferenceUnavailable,
    SessionIdempotencyConflict,
    SessionUnavailable,
)
from src.domains.agentverse.manuscripts.tests.test_manuscript_runtime import (
    ACTOR,
    FULL,
    KEY,
    MANUSCRIPT,
    Clock,
    FakeCatalog,
    rk,
    say,
)
from src.domains.agentverse.tests.agents.test_creation_drafts import DraftMongo

T0 = datetime(2026, 10, 31, 9, 0, tzinfo=UTC)
DRAFT = "draft-owned"


class Env:
    def __init__(self) -> None:
        self.draft_store = DraftMongo()
        self.drafts = MongoAgentDraftRepository(self.draft_store)
        self.session_store = DraftMongo()
        registry = ManuscriptRegistry("manuscripts-draft-refs")
        register_builtin(registry)
        registry.freeze()
        self.reader = DraftReferenceReader(self.drafts)
        self.service = ManuscriptService(
            registry=registry,
            repository=MongoManuscriptSessionRepository(self.session_store),
            catalog=FakeCatalog(),
            drafts=self.reader,
            clock=Clock(),
        )
        self.heads: dict[str, AgentDraftRevision] = {}

    async def commit(
        self,
        draft_id: str = DRAFT,
        *,
        tenant: str = "tenant",
        owner: str = "owner",
        source: str = "enki",
        status: str = "editing",
    ) -> AgentDraftRevision:
        """Commit the next real revision of ``draft_id`` with ``status``."""
        parent = self.heads.get(draft_id)
        revision = 1 if parent is None else parent.revision + 1
        cause = None
        if status == "approved":
            cause = RevisionCause(
                kind="approval", approval_id="ap-1", review_digest="1" * 64, reviewed_revision=1
            )
        elif status == "consumed":
            cause = RevisionCause(
                kind="consumption",
                approval_id="ap-1",
                review_digest="1" * 64,
                reviewed_revision=1,
                creation_id="cr-1",
                agent_id="agent-1",
            )
        record = AgentDraftRevision.build(
            draft_id=draft_id,
            revision=revision,
            tenant_id=tenant,
            owner_subject_id=owner,
            source_agent_id=source,
            status=status,
            content={"name": "Contract Summarizer"},
            provenance={"/name": FieldProvenance(origin="user", revision=1)},
            creation_request_digest="0" * 64,
            parent_digest=None if parent is None else parent.revision_digest,
            created_at=T0,
            committed_at=T0,
            cause=cause,
        )
        await self.drafts.commit(record)
        self.heads[draft_id] = record
        return record

    def draft_documents(self) -> dict[str, Any]:
        return copy.deepcopy(
            {name: c.documents for name, c in self.draft_store.collections.items()}
        )


@pytest.fixture
def env() -> Env:
    return Env()


async def start(e: Env, draft_id: str | None = DRAFT, key: str = KEY, actor=ACTOR):
    return await e.service.start(actor, MANUSCRIPT, idempotency_key=key, draft_id=draft_id)


@pytest.mark.asyncio
async def test_sessions_without_a_draft_remain_valid_and_never_read_drafts(env):
    session, _ = await start(env, None)
    assert session.draft_id is None and session.status == "active"
    assert env.draft_store.collections == {}  # No draft read at all.
    # Even a service without a reader starts draft-less sessions.
    plain = ManuscriptService(
        registry=env.service._registry,
        repository=env.service._repository,
        catalog=FakeCatalog(),
    )
    other, _ = await plain.start(ACTOR, MANUSCRIPT, idempotency_key="no-draft-key-00001")
    assert other.draft_id is None


@pytest.mark.asyncio
async def test_the_actors_own_editable_draft_binds_with_minimal_metadata(env):
    await env.commit()
    reference = await env.service.draft(ACTOR, DRAFT, editable=True)
    assert reference == DraftReference(DRAFT, 1, "editing")
    assert set(vars(reference)) == {"draft_id", "revision", "status"}  # No content.
    session, _ = await start(env)
    assert session.draft_id == DRAFT


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case",
    ["missing", "foreign_tenant", "other_human", "wrong_source_agent"],
)
async def test_missing_and_unauthorized_drafts_are_indistinguishable(env, case):
    if case == "foreign_tenant":
        await env.commit(tenant="other-tenant")
    elif case == "other_human":
        await env.commit(owner="someone")
    elif case == "wrong_source_agent":
        await env.commit(source="thoth")
    errors = []
    for _ in range(2):
        with pytest.raises(DraftReferenceUnavailable) as raised:
            await start(env)
        errors.append((type(raised.value), raised.value.code, str(raised.value)))
    assert errors[0] == (DraftReferenceUnavailable, "session.draft_unavailable", "Draft not found")
    assert env.session_store.documents(SESSIONS_COLLECTION) == {}  # Nothing bound.


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["approved", "consumed"])
async def test_an_owned_frozen_draft_is_not_editable(env, status):
    await env.commit()
    await env.commit(status=status)
    with pytest.raises(DraftNotEditable) as raised:
        await start(env)
    assert raised.value.code == "session.draft_not_editable"
    assert env.session_store.documents(SESSIONS_COLLECTION) == {}
    # The same status on a foreign draft is never revealed.
    await env.commit("foreign", owner="someone")
    await env.commit("foreign", owner="someone", status=status)
    with pytest.raises(DraftReferenceUnavailable):
        await start(env, "foreign", key="foreign-key-000001")


@pytest.mark.asyncio
async def test_identity_is_server_side_only(env):
    await env.commit()
    # Another actor (other human or other Agent) cannot bind the owner's draft by naming it.
    for actor in (
        ManuscriptActor(
            "tenant", "someone", "enki", "agent", User(id="someone", tenant_id="tenant")
        ),
        ManuscriptActor("tenant", "owner", "thoth", "agent", User(id="owner", tenant_id="tenant")),
        ManuscriptActor("other", "owner", "enki", "agent", User(id="owner", tenant_id="other")),
    ):
        with pytest.raises(DraftReferenceUnavailable):
            await start(env, actor=actor)


@pytest.mark.asyncio
async def test_replay_after_a_status_change_returns_the_unchanged_original(env):
    await env.commit()
    session, _ = await start(env)
    stored = copy.deepcopy(env.session_store.documents(SESSIONS_COLLECTION))
    await env.commit(status="approved")
    replay, _ = await start(env)
    # Historical replay: identical session, identity, fingerprint and digests.
    assert replay == session
    assert env.session_store.documents(SESSIONS_COLLECTION) == stored
    # A new binding (new key) to the now-frozen draft is refused.
    with pytest.raises(DraftNotEditable):
        await start(env, key="second-key-0000001")
    # A different draft under the same key is still an idempotency conflict.
    with pytest.raises(SessionIdempotencyConflict):
        await start(env, "another-draft")


@pytest.mark.asyncio
async def test_restart_rechecks_new_successors_but_replays_history(env):
    await env.commit()
    session, _ = await start(env)
    successor = await env.service.restart(ACTOR, session.session_id, request_key=rk())
    assert successor.draft_id == DRAFT and successor.restarted_from == session.session_id
    # The draft freezes; restarting the successor would bind a stale reference: refused.
    await env.commit(status="approved")
    with pytest.raises(DraftNotEditable):
        await env.service.restart(ACTOR, successor.session_id, request_key=rk())
    current, _ = await env.service.owned(ACTOR, successor.session_id)
    assert current == successor  # Unchanged.
    # Replaying the already recorded restart returns the original successor unchanged.
    again = await env.service.restart(ACTOR, session.session_id, request_key=rk())
    assert again == successor


@pytest.mark.asyncio
async def test_completion_rechecks_the_draft_and_replay_is_history(env):
    await env.commit()
    session, _ = await start(env)
    for answers in FULL:
        session, outcome = await say(env, session, answers)
        assert outcome.status == "accepted"
    key = rk()
    done = await env.service.complete(
        ACTOR, session.session_id, expected_revision=session.revision, request_key=key
    )
    assert done.status == "completed" and done.artifact_digest
    await env.commit(status="approved")
    replay = await env.service.complete(
        ACTOR, session.session_id, expected_revision=session.revision, request_key=key
    )
    assert replay == done  # Same artifact and digest; no re-validation rewrites history.

    # A new completion while the draft is frozen is refused before any change.
    await env.commit("draft-2")
    fresh, _ = await start(env, "draft-2", key="draft-two-key-0001")
    for answers in FULL:
        fresh, _ = await say(env, fresh, answers)
    await env.commit("draft-2", status="approved")
    with pytest.raises(DraftNotEditable):
        await env.service.complete(
            ACTOR, fresh.session_id, expected_revision=fresh.revision, request_key=rk()
        )
    assert (await env.service.owned(ACTOR, fresh.session_id))[0] == fresh


@pytest.mark.asyncio
async def test_concurrent_draft_revisions_are_observed_not_pinned(env):
    await env.commit()
    session, _ = await start(env)
    await env.commit()  # A human edit (revision 2) after the binding.
    reference = await env.service.draft(ACTOR, DRAFT, editable=True)
    assert reference.revision == 2  # The current revision, for the coordinator's CAS.
    assert session.draft_id == DRAFT and "draft_revision" not in session.model_dump()


@pytest.mark.asyncio
async def test_validation_never_mutates_drafts(env):
    await env.commit()
    await env.commit()
    before = env.draft_documents()
    await start(env)
    await env.service.draft(ACTOR, DRAFT, editable=True)
    with pytest.raises(DraftReferenceUnavailable):
        await start(env, "missing-draft", key="missing-key-000001")
    assert env.draft_documents() == before
    assert not hasattr(env.service, "_drafts")  # No draft-write handle.
    assert [n for n in dir(env.reader) if not n.startswith("__")] == ["_repository", "reference"]


@pytest.mark.asyncio
async def test_an_unavailable_draft_store_is_retryable_and_binds_nothing(env, monkeypatch):
    async def down(*args, **kwargs):
        raise DraftUnavailable("Draft store is unavailable")

    monkeypatch.setattr(env.drafts, "current", down)
    with pytest.raises(SessionUnavailable) as raised:
        await start(env)
    assert raised.value.retryable
    assert env.session_store.documents(SESSIONS_COLLECTION) == {}


@pytest.mark.asyncio
async def test_a_service_without_a_reader_fails_closed_for_attached_drafts(env):
    plain = ManuscriptService(
        registry=env.service._registry,
        repository=env.service._repository,
        catalog=FakeCatalog(),
    )
    await env.commit()
    with pytest.raises(DraftReferenceUnavailable):
        await plain.start(ACTOR, MANUSCRIPT, idempotency_key=KEY, draft_id=DRAFT)
