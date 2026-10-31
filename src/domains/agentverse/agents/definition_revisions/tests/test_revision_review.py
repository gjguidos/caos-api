"""REV-2: governed revision drafts and digest-bound reviews.

Real (disposable) MongoDB for definitions, revisions, drafts and reviews; real UTU/CPBX
scoped authority, the real ``data.validation`` provider, composer and Conversation gate.
Nothing approves, applies or writes an Agent definition.

Requires ``OPS2_MONGO_INTEGRATION=1``.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from dependency_injector import providers
from fastapi import FastAPI
from pydantic import ValidationError

from src.base.entities.user import User
from src.base.events.v2.contracts import EventPermissionError
from src.base.handlers.exception import register_exception_handlers
from src.domains.agentverse.activities.registry import (
    ActivityContractRegistry,
    ResourceResolverRegistry,
)
from src.domains.agentverse.activities.tests.test_activity_contracts import Resolver
from src.domains.agentverse.agents.composition import AgentComposer
from src.domains.agentverse.agents.conversational_composition_v2 import ConversationGate
from src.domains.agentverse.agents.creation_drafts.activity import AGENT_CREATION_INTERVIEW
from src.domains.agentverse.agents.definition_revisions.baseline import (
    baseline_revision,
    verify_baseline,
)
from src.domains.agentverse.agents.definition_revisions.drafts import (
    MongoRevisionDraftRepository,
    RevisionDraftConflict,
    RevisionDraftCreateRequest,
    RevisionDraftIdempotencyConflict,
    RevisionDraftNotFound,
    RevisionDraftUpdateRequest,
)
from src.domains.agentverse.agents.definition_revisions.gateway import (
    AgentRevisionGateway,
    RevisionGatewayBinding,
)
from src.domains.agentverse.agents.definition_revisions.models import (
    REVISIONS_COLLECTION,
    AgentDefinitionRevision,
    Predecessor,
)
from src.domains.agentverse.agents.definition_revisions.repository import (
    MongoAgentDefinitionRevisionRepository,
)
from src.domains.agentverse.agents.definition_revisions.review import (
    MongoRevisionReviewRepository,
    RevisionReviewNotCurrent,
)
from src.domains.agentverse.agents.definition_revisions.service import (
    AgentDefinitionRevisionService,
    RevisionBaselineUnavailable,
    RevisionTargetNotFound,
)
from src.domains.agentverse.interfaces.api.v2 import agent_definition_revisions as routes
from src.domains.agentverse.ncsc.tests.test_scoped_authority import KEY, VALIDATE, environment
from src.domains.agentverse.operations.admission import permit_provenance
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey
from src.domains.agentverse.security.revocation import ExecutionPermit
from src.domains.agentverse.services.db_service import DBService
from src.domains.agentverse.services.workflow_service import WorkflowService
from src.domains.agentverse.tests.agents.test_agent_composition_v2 import catalog as backend_catalog
from src.domains.agentverse.tests.tools.test_data_validation_tool import (
    FakeWorkflowDB,
    catalog,
    put,
)
from src.domains.agentverse.tests.tools.test_store_tool import definition
from src.domains.agentverse.tools.conversation_tool.spec import ConversationToolConfig
from src.domains.agentverse.tools.store_tool.models import (
    ProvisionedIdentity,
    StoreWriteContext,
)
from src.domains.agentverse.tools.store_tool.persistence.agent_definition_repository import (
    AgentDefinitionRepository,
)
from src.domains.agentverse.tools.store_tool.store.agent_store import AgentDefinitionStore
from src.domains.agentverse.tools.validation_tool import register_data_validation
from src.domains.agentverse.tools.validation_tool.dependencies.di_container import (
    ValidationToolContainer,
)

T0 = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
INTERVIEW = "agent.creation_interview@1.0.0"
CHAT = [{"type": "chat", "key": "conversation", "default": True}]


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        self.now += timedelta(milliseconds=1)
        return self.now


def activity_gate() -> ConversationGate:
    resolvers = ResourceResolverRegistry()
    for kind in ("manuscript.session", "agent.creation_draft", "agent.draft.proposal"):
        resolvers.register(Resolver(kind))
    contracts = ActivityContractRegistry(resolvers, frozenset({"conversation.v2"}))
    contracts.register(AGENT_CREATION_INTERVIEW)
    contracts.freeze()
    gate = ConversationGate(definitions=None)
    gate.bind_activities(contracts)
    return gate


def candidate(**changes):
    value = {
        "name": "Nabu",
        "type": "chat",
        "role_name": "general",
        "personality_key": "empty_eva",
        "prompt": "You are Nabu.",
        "visibility": "private",
        "whitelisted_groups": [],
        "tools": [{"key": "conversation", "config": {"modes": ["context", "llm"]}}],
        "workflows": [],
        "rule_pack_ids": ["gdpr"],
        "gui_components": CHAT,
    }
    value.update(changes)
    return value


@pytest_asyncio.fixture
async def rev(monkeypatch):
    if os.environ.get("OPS2_MONGO_INTEGRATION") != "1":
        pytest.skip("Set OPS2_MONGO_INTEGRATION=1 for real Mongo revision tests")
    from src.base.infrastructure.db.mongoDB.isolation import isolated_mongo_repository
    from src.domains.agentverse.registries.registries import registries

    env = environment()
    catalogs = catalog()
    put(
        catalogs,
        "tool",
        "conversation",
        config_schema="src.domains.agentverse.tools.conversation_tool.spec.ConversationToolConfig",
        config=ConversationToolConfig().model_dump(),
    )
    for kind, registry in catalogs.items():
        monkeypatch.setitem(registries, kind, registry)
    container = ValidationToolContainer(
        registries=providers.Object(catalogs),
        workflow_service=providers.Object(WorkflowService(FakeWorkflowDB([]))),
        utu_authority=providers.Object(env.authority),
    )
    env.registry.unregister(VALIDATE.signal)
    register_data_validation(env.registry, container.tool())
    async with isolated_mongo_repository("revisions") as mongo:
        store = AgentDefinitionStore(persistence=AgentDefinitionRepository(mongo))
        creator = AgentInstanceKey(KEY.tenant_id, KEY.user_id, KEY.agent_id, "creation")
        context = StoreWriteContext(
            provenance=permit_provenance(ExecutionPermit(creator, ("p", "i", "0", "0")), "s"),
            verify=AsyncMock(),
        )
        created = await store.create(
            definition(
                name="Nabu",
                system_name="nabu",
                role_name="general",
                personality_key="empty_eva",
                prompt="You are Nabu.",
                rule_pack_ids=["gdpr"],
                gui_components=CHAT,
            ),
            idempotency_key="nabu-1",
            context=context,
        )
        agents = await mongo.get_collection(DBService.AGENTS_COL)

        async def holders(agent_id):
            return await agents.find({"id": agent_id}, {"_id": 0}).to_list(None)

        clock = Clock()
        revisions = MongoAgentDefinitionRevisionRepository(mongo)
        provisions = {}
        service = AgentDefinitionRevisionService(
            gateway=AgentRevisionGateway(
                bindings=(
                    RevisionGatewayBinding(
                        tenant_id=KEY.tenant_id, subject_id=KEY.user_id, executor_id=KEY.agent_id
                    ),
                ),
                authority=env.authority,
                exchange=env.exchange,
                composer=AgentComposer(catalog=backend_catalog),
            ),
            holders=holders,
            revisions=revisions,
            drafts=MongoRevisionDraftRepository(mongo),
            reviews=MongoRevisionReviewRepository(mongo),
            provisions=provisions,
            conversation=activity_gate().resolve,
            clock=clock,
        )
        yield SimpleNamespace(
            env=env,
            session=env.session,
            mongo=mongo,
            agents=agents,
            agent_id=created.resource_id,
            service=service,
            revisions=revisions,
            provisions=provisions,
            clock=clock,
        )


async def snapshot(r) -> list:
    rows = await r.agents.find({}, {"_id": 0}).to_list(None)
    return json.loads(json.dumps(rows, default=str))


def create(**changes):
    return RevisionDraftCreateRequest(
        contract="caos.agent_definition_revision_draft_create",
        schema_version=1,
        candidate=None if changes.get("current") else candidate(**changes),
    )


def update(expected, **changes):
    return RevisionDraftUpdateRequest(
        contract="caos.agent_definition_revision_draft_update",
        schema_version=1,
        expected_revision=expected,
        candidate=candidate(**changes),
    )


KEY_1 = "draft-key-0000000001"


# --- Baselines and predecessors -------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_verified_baseline_is_a_predecessor_without_creating_revision_1(rev):
    r = rev
    before = await snapshot(r)
    baseline = await r.service.baseline(r.session, r.agent_id)
    assert (baseline["status"], baseline["revision_ready"]) == ("verified", True)
    assert baseline["predecessor"]["revision"] == 1
    assert baseline["predecessor"]["materialized"] is False
    draft, created = await r.service.create_draft(r.session, r.agent_id, create(), KEY_1)
    assert created and draft.predecessor.materialized is False
    review = await r.service.review(r.session, r.agent_id, draft.draft_id, 1)
    assert review.eligible
    # Reading, authoring and reviewing never materialize revision 1 or touch the Agent.
    names = await r.agents.database.list_collection_names()
    assert REVISIONS_COLLECTION not in names
    assert await snapshot(r) == before


@pytest.mark.asyncio
async def test_a_materialized_revision_1_has_the_same_digests(rev):
    r = rev
    unmaterialized = (await r.service.baseline(r.session, r.agent_id))["predecessor"]
    holders = await r.agents.find({"id": r.agent_id}, {"_id": 0}).to_list(None)
    verification = verify_baseline(KEY.tenant_id, r.agent_id, holders)
    stored, _ = await r.revisions.insert(
        baseline_revision(verification, holders[0], recorded_at=datetime.now(UTC))
    )
    materialized = (await r.service.baseline(r.session, r.agent_id))["predecessor"]
    assert materialized == {**unmaterialized, "materialized": True}
    assert materialized["revision_digest"] == stored.revision_digest


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change,status",
    [
        ({"$set": {"prompt": "Silently changed."}}, "unverified"),
        (
            {"$set": {"prompt": "Patched.", "updated_at": datetime(2026, 11, 1, tzinfo=UTC)}},
            "conflicted",
        ),
        ({"$unset": {"store": ""}}, "unavailable"),
    ],
)
async def test_unverified_baselines_cannot_be_revised(rev, change, status):
    r = rev
    await r.agents.update_one({"id": r.agent_id}, change)
    with pytest.raises(RevisionBaselineUnavailable) as denied:
        await r.service.create_draft(r.session, r.agent_id, create(), KEY_1)
    assert denied.value.code == f"revision.baseline_{status}"


@pytest.mark.asyncio
async def test_a_projection_that_differs_from_its_latest_revision_cannot_be_revised(rev):
    r = rev
    holders = await r.agents.find({"id": r.agent_id}, {"_id": 0}).to_list(None)
    first, _ = await r.revisions.insert(
        baseline_revision(
            verify_baseline(KEY.tenant_id, r.agent_id, holders), holders[0], recorded_at=T0
        )
    )
    # A selected successor that was never applied to the projection (REV-4 absent).
    await r.revisions.insert(
        AgentDefinitionRevision.build(
            tenant_id=KEY.tenant_id,
            agent_id=r.agent_id,
            revision=2,
            origin="revision",
            predecessor=Predecessor(revision=1, revision_digest=first.revision_digest),
            definition={**first.definition, "prompt": "Selected, not applied."},
            recorded_at=T0,
        )
    )
    with pytest.raises(RevisionBaselineUnavailable) as denied:
        await r.service.create_draft(r.session, r.agent_id, create(), KEY_1)
    assert denied.value.codes == ("projection.mismatch",)


# --- Drafts ---------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drafts_start_from_the_complete_current_definition_and_are_idempotent(rev):
    r = rev
    first, created = await r.service.create_draft(
        r.session, r.agent_id, create(current=True), KEY_1
    )
    assert created and first.candidate["name"] == "Nabu" and first.candidate["tools"] == []
    assert set(first.candidate) >= {"name", "type", "role_name", "personality_key", "prompt"}
    again, created = await r.service.create_draft(
        r.session, r.agent_id, create(current=True), KEY_1
    )
    assert not created and again == first
    with pytest.raises(RevisionDraftIdempotencyConflict):
        await r.service.create_draft(r.session, r.agent_id, create(), KEY_1)
    assert first.public()["authorizes"] == {
        "revision": False,
        "approval": False,
        "application": False,
    }


@pytest.mark.parametrize(
    "forged",
    [
        {"tenant_id": "x"},
        {"id": "x"},
        {"runtime_version": "v2"},
        {"grants": ["store.create"]},
        {"creator": "x"},
        {"llm_type": "x"},
        {"store": {}},
    ],
)
def test_candidates_cannot_carry_identity_or_authority(forged):
    with pytest.raises(ValidationError):
        RevisionDraftCreateRequest(
            contract="caos.agent_definition_revision_draft_create",
            schema_version=1,
            candidate={**candidate(), **forged},
        )
    with pytest.raises(ValidationError):  # Complete candidates only: never a patch.
        RevisionDraftUpdateRequest(
            contract="caos.agent_definition_revision_draft_update",
            schema_version=1,
            expected_revision=1,
            candidate={"prompt": "Only this."},
        )


@pytest.mark.asyncio
async def test_draft_edits_are_compare_and_set_with_one_concurrent_winner(rev):
    r = rev
    first, _ = await r.service.create_draft(r.session, r.agent_id, create(), KEY_1)
    second = await r.service.update_draft(
        r.session, r.agent_id, first.draft_id, update(1, prompt="Two.")
    )
    assert (second.revision, second.parent_digest) == (2, first.state_digest)
    # An identical retry of the committed edit is idempotent; a different stale edit is not.
    assert (
        await r.service.update_draft(
            r.session, r.agent_id, first.draft_id, update(1, prompt="Two.")
        )
        == second
    )
    with pytest.raises(RevisionDraftConflict) as stale:
        await r.service.update_draft(
            r.session, r.agent_id, first.draft_id, update(1, prompt="Other.")
        )
    assert stale.value.current_revision == 2
    outcomes = await asyncio.gather(
        *(
            r.service.update_draft(
                r.session, r.agent_id, first.draft_id, update(2, prompt=f"Edit {i}.")
            )
            for i in range(8)
        ),
        return_exceptions=True,
    )
    winners = [o for o in outcomes if not isinstance(o, Exception)]
    assert len(winners) == 1 and winners[0].revision == 3
    assert all(isinstance(o, RevisionDraftConflict) for o in outcomes if isinstance(o, Exception))


# --- Authority ------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_foreign_owners_tenants_and_hidden_targets_are_denied(rev):
    r = rev
    draft, _ = await r.service.create_draft(r.session, r.agent_id, create(), KEY_1)
    # No revision gateway for another human, and bootstrap sessions never qualify.
    with pytest.raises(EventPermissionError):
        await r.service.baseline(dataclasses.replace(r.session, user_id="intruder"), r.agent_id)
    with pytest.raises(EventPermissionError):
        await r.service.baseline(dataclasses.replace(r.session, origin="bootstrap"), r.agent_id)
    # Another creator's Agent, a V1 definition, a foreign tenant and a missing ID look alike.
    for change in (
        {"$set": {"creator": "someone-else"}},
        {"$set": {"tenant_id": "other-tenant"}},
    ):
        await r.agents.update_one({"id": r.agent_id}, change)
        with pytest.raises(RevisionTargetNotFound):
            await r.service.get_draft(r.session, r.agent_id, draft.draft_id)
        await r.agents.update_one(
            {"id": r.agent_id}, {"$set": {"creator": KEY.user_id, "tenant_id": KEY.tenant_id}}
        )
    with pytest.raises(RevisionTargetNotFound):
        await r.service.baseline(r.session, "missing-agent")
    with pytest.raises(RevisionDraftNotFound):
        await r.service.get_draft(r.session, r.agent_id, "00000000-0000-4000-8000-00000000dead")


# --- Validation and review ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_eligible_review_is_digest_bound_safe_and_authorizes_nothing(rev):
    r = rev
    before = await snapshot(r)
    grants = {scope: sorted(g.invoke) for scope, g in r.env.policy._grants.items()}
    draft, _ = await r.service.create_draft(
        r.session,
        r.agent_id,
        create(tools=[{"key": "conversation", "config": {"activities": [INTERVIEW]}}]),
        KEY_1,
    )
    review = await r.service.review(r.session, r.agent_id, draft.draft_id, 1)
    envelope = review.envelope
    assert review.eligible and envelope["review_digest"] == review.review_digest
    assert "tools" in envelope["changed_fields"] and "name" not in envelope["changed_fields"]
    assert envelope["validation"]["resolved"] is None
    assert (
        not {"llm_type", "db_type", "tenant_id", "creator"}
        & envelope["composition"]["preview"].keys()
    )
    assert envelope["authorizes"] == {"approval": False, "revision": False, "application": False}
    assert envelope["capability"]["operation"] == "agent.definition_revision.validate"
    status = await r.service.review_status(r.session, r.agent_id, review.review_id)
    assert status["state"] == "current"
    current = await r.service.require_current(r.session, r.agent_id, review.review_id)
    assert current.review_id == review.review_id and current.snapshot == review.snapshot
    # Valid attachment without any execution grant: the Tool grants nothing.
    assert {scope: sorted(g.invoke) for scope, g in r.env.policy._grants.items()} == grants
    assert not [s for signals in grants.values() for s in signals if "conversation" in s]
    assert await snapshot(r) == before
    names = await r.agents.database.list_collection_names()
    assert REVISIONS_COLLECTION not in names and "coll_agent_creation_review_approvals" not in names


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes,code",
    [
        ({"tools": []}, "revision.conversation_required"),  # Conversation GUI, no Tool
        (
            {"tools": [{"key": "conversation", "config": {"activities": ["unknown.thing@1.0.0"]}}]},
            "revision.conversation_activity_unavailable",
        ),
        ({"type": "form", "role_name": "general"}, "revision.type_immutable"),
        ({"tools": [{"key": "foreign_tool", "config": {}}]}, "reference.unavailable"),
        ({"role_name": "foreign_role"}, "reference.unavailable"),
    ],
)
async def test_invalid_candidates_produce_ineligible_reviews(rev, changes, code):
    r = rev
    draft, _ = await r.service.create_draft(r.session, r.agent_id, create(**changes), KEY_1)
    review = await r.service.review(r.session, r.agent_id, draft.draft_id, 1)
    assert not review.eligible and review.review_digest is None
    assert code in review.envelope["eligibility"]["reasons"], review.envelope["eligibility"]
    assert (await r.service.review_status(r.session, r.agent_id, review.review_id))[
        "state"
    ] == "ineligible"
    with pytest.raises(RevisionReviewNotCurrent):
        await r.service.require_current(r.session, r.agent_id, review.review_id)


@pytest.mark.asyncio
async def test_unchanged_candidates_and_pinned_identities_are_ineligible(rev):
    r = rev
    current, _ = await r.service.create_draft(r.session, r.agent_id, create(current=True), KEY_1)
    review = await r.service.review(r.session, r.agent_id, current.draft_id, 1)
    assert "revision.no_change" in review.envelope["eligibility"]["reasons"]
    # Policy pins are fixed at trusted construction; the test injects one.
    r.service._provisions["nabu"] = ProvisionedIdentity(
        slot="nabu",
        resource_type="agent",
        agent_id=r.agent_id,
        tenant_id=KEY.tenant_id,
        principal_id=KEY.agent_id,
        subject_id=KEY.user_id,
        name="Nabu",
        type="chat",
        runtime_version="v2",
        role_name="general",
        personality_key="empty_eva",
    )
    renamed, _ = await r.service.create_draft(
        r.session, r.agent_id, create(name="Renamed"), "draft-key-0000000002"
    )
    review = await r.service.review(r.session, r.agent_id, renamed.draft_id, 1)
    assert "revision.pinned_identity" in review.envelope["eligibility"]["reasons"]


@pytest.mark.asyncio
async def test_expired_stale_candidates_and_changed_predecessors_fail_closed(rev):
    r = rev
    draft, _ = await r.service.create_draft(r.session, r.agent_id, create(), KEY_1)
    with pytest.raises(RevisionDraftConflict):
        await r.service.review(r.session, r.agent_id, draft.draft_id, 2)
    review = await r.service.review(r.session, r.agent_id, draft.draft_id, 1)
    # Expiry.
    r.clock.now += timedelta(minutes=11)
    assert (await r.service.review_status(r.session, r.agent_id, review.review_id))[
        "state"
    ] == "expired"
    r.clock.now -= timedelta(minutes=11)
    # A newer draft revision makes the review stale.
    await r.service.update_draft(r.session, r.agent_id, draft.draft_id, update(1, prompt="Newer."))
    assert (await r.service.review_status(r.session, r.agent_id, review.review_id))[
        "state"
    ] == "stale_candidate"
    with pytest.raises(RevisionReviewNotCurrent) as stale:
        await r.service.require_current(r.session, r.agent_id, review.review_id)
    assert stale.value.code == "revision_review.stale_candidate"
    # A predecessor that changed (here: revision 1 materialized and revision 2 selected)
    # invalidates the draft's reviews and refuses new ones.
    second = await r.service.review(r.session, r.agent_id, draft.draft_id, 2)
    holders = await r.agents.find({"id": r.agent_id}, {"_id": 0}).to_list(None)
    first, _ = await r.revisions.insert(
        baseline_revision(
            verify_baseline(KEY.tenant_id, r.agent_id, holders), holders[0], recorded_at=T0
        )
    )
    await r.revisions.insert(
        AgentDefinitionRevision.build(
            tenant_id=KEY.tenant_id,
            agent_id=r.agent_id,
            revision=2,
            origin="revision",
            predecessor=Predecessor(revision=1, revision_digest=first.revision_digest),
            definition={**first.definition, "prompt": "Concurrent."},
            recorded_at=T0,
        )
    )
    assert (await r.service.review_status(r.session, r.agent_id, second.review_id))[
        "state"
    ] == "stale_predecessor"
    with pytest.raises((RevisionReviewNotCurrent, RevisionBaselineUnavailable)):
        await r.service.review(r.session, r.agent_id, draft.draft_id, 2)


@pytest.mark.asyncio
async def test_concurrent_predecessor_change_during_review_fails_closed(rev):
    r = rev
    draft, _ = await r.service.create_draft(r.session, r.agent_id, create(), KEY_1)
    original = r.service._gateway.validate

    async def validate_while_the_agent_changes(auth, creation, check):
        await r.agents.update_one(
            {"id": r.agent_id},
            {
                "$set": {
                    "prompt": "Changed mid-review.",
                    "updated_at": datetime(2026, 11, 1, tzinfo=UTC),
                }
            },
        )
        return await original(auth, creation, check)

    r.service._gateway.validate = validate_while_the_agent_changes
    with pytest.raises((RevisionReviewNotCurrent, RevisionBaselineUnavailable)):
        await r.service.review(r.session, r.agent_id, draft.draft_id, 1)
    assert (
        "coll_agent_definition_revision_reviews"
        not in await r.agents.database.list_collection_names()
        or (
            await (
                await r.mongo.get_collection("coll_agent_definition_revision_reviews")
            ).count_documents({})
            == 0
        )
    )


# --- Routes ---------------------------------------------------------------------------------


def code(response) -> str:
    body = response.json()
    detail = body.get("detail", body.get("message"))
    return detail["code"]


@pytest.mark.asyncio
async def test_routes_map_typed_outcomes_without_revealing_targets(rev, monkeypatch):
    r = rev

    @asynccontextmanager
    async def session(_request):
        yield r.session

    monkeypatch.setattr(routes, "authenticated_genesis", session)
    monkeypatch.setattr(routes, "_token", lambda _request: "token")
    monkeypatch.setattr(routes, "_check_permissions", AsyncMock())
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(routes.router)
    app.state.agent_definition_revisions = r.service
    base = f"/api/v2/agentverse/agents/{r.agent_id}/definition-revisions"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    ) as http:
        body = create().model_dump(mode="json")
        # The existing agent.write route policy: a requester without the user role is denied.
        denied = await http.post(f"{base}/drafts", json=body, headers={"Idempotency-Key": KEY_1})
        assert denied.status_code == 403
        r.env.sessions.resource_requester.return_value = User(
            id=KEY.user_id, tenant_id=KEY.tenant_id, roles=["agentverse-user"]
        )
        created = await http.post(f"{base}/drafts", json=body, headers={"Idempotency-Key": KEY_1})
        assert created.status_code == 201, created.text
        draft_id = created.json()["draft_id"]
        stale = await http.patch(
            f"{base}/drafts/{draft_id}",
            json=update(5, prompt="x").model_dump(mode="json"),
        )
        assert (stale.status_code, code(stale)) == (409, "revision_draft.revision_conflict")
        reviewed = await http.post(
            f"{base}/drafts/{draft_id}/reviews",
            json={
                "contract": "caos.agent_definition_revision_review_request",
                "schema_version": 1,
                "expected_revision": 1,
            },
        )
        assert reviewed.status_code == 201 and reviewed.json()["state"] == "current"
        status = await http.get(f"{base}/reviews/{reviewed.json()['review_id']}")
        assert status.json()["state"] == "current"
        missing = await http.get("/api/v2/agentverse/agents/missing/definition-revisions/baseline")
        assert (missing.status_code, code(missing)) == (404, "revision.not_found")
        assert "missing" not in json.dumps(missing.json().get("message"))


def test_no_application_authority_signals_or_grants_exist():
    from src.domains.agentverse.registries import synaptic_registry_instance

    signals = synaptic_registry_instance.keys()  # Registry is not iterable
    assert not [s for s in signals if "revis" in s]
    with open("config/genesis.local.json") as file:
        policy = json.load(file)
    assert not [s for g in policy["grants"] for s in g["invoke"] if "revis" in s]
    assert policy["revision_gateways"] == [
        {
            "tenant_id": "caos-local",
            "subject_id": "00000000-0000-4000-8000-000000000001",
            "executor_id": "00000000-0000-4000-8000-000000000003",
        }
    ]
    assert not policy["trusted_lines"]
