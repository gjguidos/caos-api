"""DRAFT-0.1 durable Agent drafts: contract, compare-and-set revisions, ownership,
integrity and the absence of any review, approval, creation or activation effect.

Real UTU/CPBX/validator gateway stack; storage is an in-memory MongoDB-like double
with the built-in unique ``_id`` (real Mongo: test_creation_drafts_integration.py).
"""

import asyncio
import copy
import itertools
from collections import defaultdict
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pydantic import ValidationError
from pymongo.errors import AutoReconnect

from src.base.entities.user import User
from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.agents.creation_drafts.lifecycle import AgentDraftLifecycleService
from src.domains.agentverse.agents.creation_drafts.models import (
    DRAFTS_COLLECTION,
    MAX_CONTENT_BYTES,
    REVISIONS_COLLECTION,
    AgentDraftContent,
    AgentDraftCreateRequest,
    AgentDraftRevision,
    AgentDraftUpdateRequest,
    CorruptDraft,
    DraftField,
    DraftIdempotencyConflict,
    DraftNotFound,
    RevisionConflict,
    draft_id,
)
from src.domains.agentverse.agents.creation_drafts.repository import MongoAgentDraftRepository
from src.domains.agentverse.agents.creation_drafts.service import AgentDraftService
from src.domains.agentverse.interfaces.api.v2 import genesis_agents
from src.domains.agentverse.ncsc.tests.test_scoped_authority import KEY
from src.domains.agentverse.security.validation_gateway import AgentDraftValidationRequest
from src.domains.agentverse.tests.agents.test_validation_gateway import (  # noqa: F401
    request,
    stack,
)
from src.domains.agentverse.tests.audit.test_caos_log import FakeCollection
from src.domains.agentverse.tools.validation_tool.models import (
    SERVER_CONTROLLED_FIELDS,
    AgentCreationInput,
)

T0 = datetime(2026, 10, 30, 9, 0, tzinfo=UTC)
CREATOR = User(id=KEY.user_id, tenant_id=KEY.tenant_id, roles=["agentverse-user", "team.admin"])
KEY_1 = "draft-key-000000001"


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        self.now += timedelta(seconds=1)
        return self.now


class DraftCollection(FakeCollection):
    def __init__(self) -> None:
        super().__init__()
        self.replace_error: Exception | None = None

    async def insert_one(self, document: dict[str, Any]) -> Any:
        await asyncio.sleep(0)  # Let concurrent writers interleave at the commit point.
        return await super().insert_one(document)

    async def replace_one(self, query: dict[str, Any], document: dict[str, Any]) -> Any:
        self.calls.append("replace_one")
        if self.replace_error is not None:
            error, self.replace_error = self.replace_error, None
            raise error
        row = self.documents.get(query["_id"])
        matched = row is not None and all(row.get(k) == v for k, v in query.items() if k != "_id")
        if matched:
            self.documents[query["_id"]] = copy.deepcopy(document)
        return SimpleNamespace(matched_count=int(matched))


def _matches(document: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, expected in query.items():
        if key == "$or":
            if not any(_matches(document, branch) for branch in expected):
                return False
            continue
        value = document.get(key)
        if isinstance(expected, dict) and "$in" in expected:
            if value not in expected["$in"]:
                return False
        elif isinstance(expected, dict) and "$lt" in expected:
            if value is None or not value < expected["$lt"]:
                return False
        elif value != expected:
            return False
    return True


class FakeCursor:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = documents

    def sort(self, keys: list[tuple[str, int]]) -> "FakeCursor":
        for key, direction in reversed(keys):
            self._documents.sort(key=lambda d: d.get(key), reverse=direction < 0)
        return self

    def limit(self, count: int) -> "FakeCursor":
        self._documents = self._documents[:count]
        return self

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        return [copy.deepcopy(d) for d in self._documents[:length]]


def _find(self: Any, query: dict[str, Any]) -> FakeCursor:
    self.calls.append("find")
    return FakeCursor([d for d in self.documents.values() if _matches(d, query)])


DraftCollection.find = _find  # type: ignore[attr-defined]


class DraftMongo:
    def __init__(self) -> None:
        self.collections: defaultdict[str, DraftCollection] = defaultdict(DraftCollection)

    async def get_collection(self, name: str) -> DraftCollection:
        return self.collections[name]

    def documents(self, name: str) -> dict[str, dict[str, Any]]:
        return self.collections[name].documents


async def allow(_requester: User) -> None:
    return None


async def deny(_requester: User) -> None:
    raise EventPermissionError("Agent creation requires a creator")


@pytest.fixture
def drafts(stack):  # noqa: F811
    stack.env.sessions.resource_requester.return_value = CREATOR
    stack.store = DraftMongo()
    stack.repository = MongoAgentDraftRepository(stack.store)
    stack.drafts = AgentDraftService(
        gateway=stack.gateway, repository=stack.repository, clock=Clock()
    )
    return stack


def create_body(**creation: Any) -> AgentDraftCreateRequest:
    return AgentDraftCreateRequest.model_validate(
        {
            "contract": "caos.agent_creation_draft",
            "schema_version": 1,
            "resource_type": "agent",
            "creation": creation or {"name": "Draft Candidate", "type": "chat"},
        }
    )


def update_body(expected: int, changes: dict | None = None, remove=()) -> AgentDraftUpdateRequest:
    return AgentDraftUpdateRequest.model_validate(
        {
            "contract": "caos.agent_draft_update",
            "schema_version": 1,
            "expected_revision": expected,
            "changes": changes or {},
            "remove": list(remove),
        }
    )


async def created(s, body=None, key=KEY_1):
    record, _ = await s.drafts.create(
        s.env.session, "source", body or create_body(), key, authorize_creator=allow
    )
    return record


async def update(s, record, expected, changes=None, remove=()):
    return await s.drafts.update(
        s.env.session,
        "source",
        record.draft_id,
        update_body(expected, changes, remove),
        authorize_creator=allow,
    )


# --- Contract -----------------------------------------------------------------------------


def test_draft_fields_are_exactly_the_agent_creation_input_fields():
    assert set(DraftField.__args__) == set(AgentCreationInput.model_fields)
    assert set(AgentDraftContent.model_fields) == set(AgentCreationInput.model_fields)


@pytest.mark.parametrize("field", sorted(SERVER_CONTROLLED_FIELDS))
def test_server_owned_fields_are_rejected_not_stripped(field):
    with pytest.raises(ValidationError):
        AgentDraftContent.model_validate({"name": "X", field: "client-value"})


@pytest.mark.parametrize(
    "extra",
    [
        {"owner_subject_id": "someone"},
        {"tenant_id": "other"},
        {"status": "approved"},
        {"revision": 9},
        {"approvals": ["a"]},
        {"draft_id": "x"},
    ],
)
def test_envelopes_reject_client_ownership_status_and_approval_metadata(extra):
    with pytest.raises(ValidationError):
        AgentDraftCreateRequest.model_validate({**create_body().model_dump(mode="json"), **extra})
    with pytest.raises(ValidationError):
        AgentDraftUpdateRequest.model_validate({**update_body(1).model_dump(mode="json"), **extra})


@pytest.mark.parametrize(
    "creation",
    [
        {"name": 7},
        {"visibility": "everyone"},
        {"tools": [{"key": "a", "unknown": 1}]},
        {"tools": "web"},
        {"workflows": ["a", 1]},
        {"gui_components": [{"type": "terminal", "key": "x"}]},
        {"prompt": "x" * (MAX_CONTENT_BYTES + 1)},
        {"name": "x" * 101},
    ],
)
def test_invalid_agent_draft_structures_are_rejected(creation):
    with pytest.raises(ValidationError):
        create_body(**creation)


def test_partial_content_is_allowed_while_editing_and_absent_fields_are_omitted():
    assert create_body(name="Only a name").creation.data() == {"name": "Only a name"}
    assert AgentDraftContent().data() == {}


def test_update_requires_a_revision_and_disjoint_changes():
    with pytest.raises(ValidationError):
        AgentDraftUpdateRequest.model_validate(
            {"contract": "caos.agent_draft_update", "schema_version": 1}
        )
    for expected in (0, "1", 1.0, True):
        with pytest.raises(ValidationError):
            update_body(expected)
    with pytest.raises(ValidationError):
        update_body(1, {"name": "A"}, ["name"])
    with pytest.raises(ValidationError):
        update_body(1, remove=["prompt", "prompt"])
    with pytest.raises(ValidationError):
        update_body(1, remove=["tenant_id"])


def test_draft_identity_is_stable_and_scoped():
    one = draft_id("t", "u", "source", KEY_1)
    assert one == draft_id("t", "u", "source", KEY_1)
    assert (
        len(
            {
                one,
                draft_id("t2", "u", "source", KEY_1),
                draft_id("t", "u2", "source", KEY_1),
                draft_id("t", "u", "source2", KEY_1),
                draft_id("t", "u", "source", KEY_1 + "x"),
            }
        )
        == 5
    )


# --- Create, read, revisions ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_and_read_use_verified_identity_and_never_trust_the_body(drafts):
    s = drafts
    record = await created(s)
    assert (record.revision, record.status) == (1, "editing")
    assert (record.tenant_id, record.owner_subject_id) == (KEY.tenant_id, KEY.user_id)
    assert record.source_agent_id == "source" and record.parent_digest is None
    assert record.content == {"name": "Draft Candidate", "type": "chat"}
    assert record.provenance["/name"].model_dump() == {"origin": "user", "revision": 1}
    read = await s.drafts.get(s.env.session, "source", record.draft_id, authorize_creator=allow)
    assert read == record
    public = read.public()
    assert public["draft"] == {
        "contract": "caos.agent_creation_draft",
        "schema_version": 1,
        "resource_type": "agent",
        "creation": {"name": "Draft Candidate", "type": "chat"},
    }
    assert not {"tenant_id", "owner_subject_id", "_id", "revision_digest"} & public.keys()
    assert public["authorizes"] == dict.fromkeys(
        ("review", "approval", "creation", "activation"), False
    )


@pytest.mark.asyncio
async def test_create_is_idempotent_per_key_and_conflicts_on_reuse(drafts):
    s = drafts
    first, made = await s.drafts.create(
        s.env.session, "source", create_body(), KEY_1, authorize_creator=allow
    )
    again, made_again = await s.drafts.create(
        s.env.session, "source", create_body(), KEY_1, authorize_creator=allow
    )
    assert (made, made_again) == (True, False) and again == first
    with pytest.raises(DraftIdempotencyConflict):
        await s.drafts.create(
            s.env.session, "source", create_body(name="Other"), KEY_1, authorize_creator=allow
        )
    # A replay after edits returns the current draft, never a reset.
    edited, _ = await update(s, first, 1, {"prompt": "Edited"})
    replay, _ = await s.drafts.create(
        s.env.session, "source", create_body(), KEY_1, authorize_creator=allow
    )
    assert replay == edited and replay.revision == 2
    assert len(s.store.documents(DRAFTS_COLLECTION)) == 1


@pytest.mark.asyncio
async def test_revisions_increment_and_track_field_provenance(drafts):
    s = drafts
    record = await created(s)
    second, changed = await update(s, record, 1, {"prompt": "Summarize contracts"})
    assert changed and second.revision == 2 and second.parent_digest == record.revision_digest
    third, _ = await update(s, second, 2, {"name": "Renamed", "type": "chat"}, ["prompt"])
    assert third.revision == 3
    assert third.content == {"name": "Renamed", "type": "chat"}
    # Unchanged values keep their original provenance; changed values name the revision.
    assert third.provenance["/type"].revision == 1
    assert third.provenance["/name"].revision == 3
    assert "/prompt" not in third.provenance
    assert third.created_at == record.created_at < third.committed_at
    # A no-op commits nothing.
    same, changed = await update(s, third, 3, {"name": "Renamed"})
    assert (same, changed) == (third, False)
    assert len(s.store.documents(REVISIONS_COLLECTION)) == 3


@pytest.mark.asyncio
async def test_committed_revisions_are_immutable_snapshots(drafts):
    s = drafts
    record = await created(s)
    stored = copy.deepcopy(s.store.documents(REVISIONS_COLLECTION))
    current = record
    for index in range(2, 6):
        current, _ = await update(s, current, index - 1, {"prompt": f"Revision {index}"})
    revisions = s.store.documents(REVISIONS_COLLECTION)
    assert {key: revisions[key] for key in stored} == stored
    one = await s.drafts.revision(
        s.env.session, "source", record.draft_id, 1, authorize_creator=allow
    )
    assert one == record
    chain = [await s.repository.revision(KEY.tenant_id, record.draft_id, n) for n in range(1, 6)]
    assert all(b.parent_digest == a.revision_digest for a, b in itertools.pairwise(chain))


@pytest.mark.asyncio
async def test_tampered_or_broken_revisions_fail_closed(drafts):
    s = drafts
    record = await created(s)
    await update(s, record, 1, {"prompt": "Second"})
    revisions = s.store.documents(REVISIONS_COLLECTION)
    original = copy.deepcopy(revisions)
    revisions[f"{record.draft_id}:1"]["content_json"] = '{"name":"Injected"}'
    with pytest.raises(CorruptDraft):
        await s.repository.revision(KEY.tenant_id, record.draft_id, 1)
    revisions.clear()
    revisions.update(original)
    revisions[f"{record.draft_id}:2"]["owner_subject_id"] = "attacker"
    with pytest.raises(CorruptDraft):
        await s.repository.revision(KEY.tenant_id, record.draft_id, 2)
    with pytest.raises(ValidationError):
        AgentDraftRevision.model_validate({**record.model_dump(), "parent_digest": "0" * 64})


# --- Concurrency ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stale_revision_is_a_conflict_with_the_current_revision(drafts):
    s = drafts
    record = await created(s)
    await update(s, record, 1, {"prompt": "First writer"})
    with pytest.raises(RevisionConflict) as raised:
        await update(s, record, 1, {"prompt": "Stale writer"})
    assert raised.value.current_revision == 2
    current = await s.drafts.get(s.env.session, "source", record.draft_id, authorize_creator=allow)
    assert current.content["prompt"] == "First writer"


@pytest.mark.asyncio
async def test_simultaneous_updates_from_one_revision_commit_exactly_once(drafts):
    s = drafts
    record = await created(s)
    results = await asyncio.gather(
        *[update(s, record, 1, {"prompt": f"Writer {n}"}) for n in range(8)],
        return_exceptions=True,
    )
    winners = [r for r in results if not isinstance(r, BaseException)]
    conflicts = [r for r in results if isinstance(r, RevisionConflict)]
    assert len(winners) == 1 and len(conflicts) == 7
    assert {c.current_revision for c in conflicts} == {2}
    # The insert itself is the gate: losers reached the commit point, not just a read.
    assert s.store.collections[REVISIONS_COLLECTION].calls.count("insert_one") == 9
    current = await s.drafts.get(s.env.session, "source", record.draft_id, authorize_creator=allow)
    assert current == winners[0][0]
    assert len(s.store.documents(REVISIONS_COLLECTION)) == 2


@pytest.mark.asyncio
async def test_a_lagging_projection_is_rolled_forward_never_lost(drafts):
    s = drafts
    record = await created(s)
    s.store.collections[DRAFTS_COLLECTION].replace_error = AutoReconnect("lost after commit")
    second, changed = await update(s, record, 1, {"prompt": "Committed"})
    assert changed and s.store.documents(DRAFTS_COLLECTION)[record.draft_id]["revision"] == 1
    # The committed revision is visible, and a writer at the stale head conflicts.
    with pytest.raises(RevisionConflict):
        await update(s, record, 1, {"prompt": "Would overwrite"})
    current = await s.drafts.get(s.env.session, "source", record.draft_id, authorize_creator=allow)
    assert current == second
    assert s.store.documents(DRAFTS_COLLECTION)[record.draft_id]["revision"] == 2


@pytest.mark.asyncio
async def test_a_missing_projection_after_the_first_commit_is_recovered(drafts):
    s = drafts
    record = await created(s)
    s.store.documents(DRAFTS_COLLECTION).clear()
    current = await s.drafts.get(s.env.session, "source", record.draft_id, authorize_creator=allow)
    assert current == record


# --- Ownership and authorization --------------------------------------------------------------


async def foreign(s, **overrides):
    """A draft stored under the same storage identity scheme but another owner/source."""
    fields = {
        "draft_id": draft_id(KEY.tenant_id, "intruder", "source", KEY_1),
        "revision": 1,
        "tenant_id": KEY.tenant_id,
        "owner_subject_id": "intruder",
        "source_agent_id": "source",
        "status": "editing",
        "content": {"name": "Not yours"},
        "provenance": {"/name": {"origin": "user", "revision": 1}},
        "creation_request_digest": "0" * 64,
        "parent_digest": None,
        "created_at": T0,
        "committed_at": T0,
        **overrides,
    }
    from src.domains.agentverse.agents.creation_drafts.models import FieldProvenance

    fields["provenance"] = {k: FieldProvenance(**v) for k, v in fields["provenance"].items()}
    record = AgentDraftRevision.build(**fields)
    await s.repository.commit(record)
    return record


@pytest.mark.asyncio
async def test_other_owners_tenants_and_sources_are_indistinguishable_from_missing(drafts):
    s = drafts
    others = [
        await foreign(s),
        await foreign(
            s,
            draft_id=draft_id("t", "u", "x", KEY_1),
            owner_subject_id=KEY.user_id,
            source_agent_id="other-source",
        ),
        await foreign(
            s,
            draft_id=draft_id("t2", "u", "x", KEY_1),
            owner_subject_id=KEY.user_id,
            tenant_id="other-tenant",
        ),
    ]
    for other in others:
        before = copy.deepcopy(s.store.documents(REVISIONS_COLLECTION))
        with pytest.raises(DraftNotFound):
            await s.drafts.get(s.env.session, "source", other.draft_id, authorize_creator=allow)
        with pytest.raises(DraftNotFound):
            await s.drafts.revision(
                s.env.session, "source", other.draft_id, 1, authorize_creator=allow
            )
        with pytest.raises(DraftNotFound):
            await update(s, other, 1, {"name": "Hijacked"})
        assert s.store.documents(REVISIONS_COLLECTION) == before
    with pytest.raises(DraftNotFound):
        await s.drafts.get(
            s.env.session, "source", draft_id("a", "b", "c", KEY_1), authorize_creator=allow
        )


@pytest.mark.asyncio
async def test_authority_is_fresh_and_fail_closed_for_every_operation(drafts):
    s = drafts
    record = await created(s)
    operations = [
        lambda session, source, authz: s.drafts.create(
            session, source, create_body(), "draft-key-000000002", authorize_creator=authz
        ),
        lambda session, source, authz: s.drafts.get(
            session, source, record.draft_id, authorize_creator=authz
        ),
        lambda session, source, authz: s.drafts.revision(
            session, source, record.draft_id, 1, authorize_creator=authz
        ),
        lambda session, source, authz: s.drafts.update(
            session,
            source,
            record.draft_id,
            update_body(1, {"prompt": "X"}),
            authorize_creator=authz,
        ),
    ]
    for operation in operations:
        with pytest.raises(EventPermissionError):  # Not a creator.
            await operation(s.env.session, "source", deny)
        with pytest.raises(EventPermissionError):  # No approved gateway binding.
            await operation(s.env.session, "other-source", allow)
        with pytest.raises(EventPermissionError):  # Bootstrap is not a human.
            await operation(replace(s.env.session, origin="bootstrap"), "source", allow)
        with pytest.raises(EventPermissionError):
            await operation(replace(s.env.session, active=False), "source", allow)
    s.source["tenant_id"] = "other"  # Source no longer readable by the requester.
    for operation in operations:
        with pytest.raises(EventPermissionError):
            await operation(s.env.session, "source", allow)
    assert len(s.store.documents(REVISIONS_COLLECTION)) == 1


# --- G3.3/G3.4 boundaries ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drafts_never_validate_approve_create_or_activate(drafts):
    s = drafts
    record = await created(s)
    await update(s, record, 1, {"prompt": "No effects"})
    # No data.validation dispatch, Store write, approval or activation path was used.
    assert set(s.store.collections) == {DRAFTS_COLLECTION, REVISIONS_COLLECTION}
    assert not hasattr(s.drafts, "_exchange") and not hasattr(s.drafts, "_approvals")
    s.definitions.find_by_id.assert_awaited_with("source")  # read-only source check


@pytest.mark.asyncio
async def test_a_complete_draft_reviews_with_the_unchanged_g33_digest(drafts):
    s = drafts
    body = request()  # The existing G3.3 client body.
    record = await created(s, AgentDraftCreateRequest.model_validate(body.model_dump(mode="json")))
    envelope = AgentDraftValidationRequest.model_validate(record.creation_draft())
    direct = await s.gateway.review(s.env.session, "source", body)
    from_draft = await s.gateway.review(s.env.session, "source", envelope)
    assert direct.eligible and from_draft.digest == direct.digest


# --- HTTP --------------------------------------------------------------------------------------


@pytest.fixture
def http(drafts, monkeypatch):
    s = drafts

    async def authenticated(_request):
        yield s.env.session

    monkeypatch.setattr(genesis_agents, "authenticated_genesis", asynccontextmanager(authenticated))
    app = FastAPI()
    app.container = SimpleNamespace(authz=SimpleNamespace(authz_client=lambda: None))
    app.state.agent_drafts = s.drafts
    app.state.agent_draft_lifecycle = AgentDraftLifecycleService(
        drafts=s.drafts,
        revisions=s.repository,
        gateway=s.gateway,
        approvals=None,  # type: ignore[arg-type]  # Editing drafts never reach G3.3/G3.4.
        creations=None,  # type: ignore[arg-type]
    )
    app.include_router(genesis_agents.router)
    s.app = app
    return s


def client(app, **headers):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": "Bearer bearer-token", **headers},
    )


BASE = "/api/v2/agentverse/agents/source/creation-drafts"


@pytest.mark.asyncio
async def test_http_create_read_update_and_conflict(http):
    s = http
    body = create_body().model_dump(mode="json")
    async with client(s.app, **{"Idempotency-Key": KEY_1}) as c:
        made = await c.post(BASE, json=body)
        assert made.status_code == 201, made.text
        draft = made.json()
        assert (await c.post(BASE, json=body)).status_code == 200
        path = f"{BASE}/{draft['draft_id']}"
        assert (await c.get(path)).json() == draft
        patch = update_body(1, {"prompt": "Over HTTP"}).model_dump(mode="json")
        updated = await c.patch(path, json=patch)
        assert updated.status_code == 200 and updated.json()["revision"] == 2
        stale = await c.patch(path, json=patch)
        assert stale.status_code == 409
        assert stale.json()["detail"] == {
            "code": "draft.revision_conflict",
            "retryable": False,
            "current_revision": 2,
        }
        assert (await c.get(f"{path}/revisions/1")).json() == draft
        assert (await c.get(f"{path}/revisions/3")).status_code == 404
        assert (await c.get(f"{path}/revisions/0")).status_code == 422
        unknown = draft_id("t", "u", "s", KEY_1)
        assert (await c.get(f"{BASE}/{unknown}")).json()["detail"]["code"] == "draft.not_found"
        assert (await c.get(f"{BASE}/not-a-uuid")).status_code == 422


@pytest.mark.asyncio
async def test_http_rejects_malformed_requests_and_non_creators(http):
    s = http
    body = create_body().model_dump(mode="json")
    async with client(s.app) as c:
        assert (await c.post(BASE, json=body)).status_code == 422  # No Idempotency-Key.
    async with client(s.app, **{"Idempotency-Key": "short"}) as c:
        assert (await c.post(BASE, json=body)).status_code == 422
    async with client(s.app, **{"Idempotency-Key": KEY_1}) as c:
        for bad in (
            {**body, "owner_subject_id": "x"},
            {**body, "status": "approved"},
            {**body, "creation": {"name": "X", "creator": "x"}},
            {**body, "contract": "caos.agent_draft"},
            {**body, "schema_version": 2},
        ):
            assert (await c.post(BASE, json=bad)).status_code == 422
        draft = (await c.post(BASE, json=body)).json()
        path = f"{BASE}/{draft['draft_id']}"
        for bad in (
            {"contract": "caos.agent_draft_update", "schema_version": 1, "changes": {}},
            {**update_body(1).model_dump(mode="json"), "approval_id": "x"},
            {**update_body(1).model_dump(mode="json"), "changes": {"tenant_id": "x"}},
        ):
            assert (await c.patch(path, json=bad)).status_code == 422
        # Not an agentverse-user: the existing creator policy denies every operation.
        s.env.sessions.resource_requester.return_value = CREATOR.model_copy(
            update={"roles": ["team.admin"]}
        )
        assert (await c.get(path)).status_code == 403
        assert (await c.patch(path, json=update_body(1).model_dump(mode="json"))).status_code == 403
        assert (await c.post(BASE, json=body)).status_code == 403
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=s.app), base_url="http://test"
    ) as anonymous:
        assert (await anonymous.get(path)).status_code == 403


@pytest.mark.asyncio
async def test_http_draft_routes_precede_the_generic_lifecycle_route(http):
    paths = [getattr(route, "path", "") for route in http.app.routes]
    lifecycle = paths.index("/api/v2/agentverse/agents/{agent_id}/{operation}")
    assert paths.index("/api/v2/agentverse/agents/{source_agent_id}/creation-drafts") < lifecycle
