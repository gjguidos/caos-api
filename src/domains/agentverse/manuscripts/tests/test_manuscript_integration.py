"""MANUSCRIPT-0.1 on disposable infrastructure: real MongoDB sessions (concurrency,
persistence, crash recovery, tamper detection) and, over the local application, the
bootstrap-registered Manuscript, an actor from Enki's verified UTU permit, the real
requester-scoped catalog and a G3.3 review of the completed artifact.

Requires ``OPS2_MONGO_INTEGRATION=1``; the application test also ``UTU_TEST_REDIS_URL``.
"""

import asyncio
import os

import pytest
import pytest_asyncio

from src.base.entities.user import User
from src.domains.agentverse.dependencies.genesis_local import ENKI, OWNER, TENANT
from src.domains.agentverse.manuscripts.definitions import register_builtin
from src.domains.agentverse.manuscripts.registry import ManuscriptRegistry
from src.domains.agentverse.manuscripts.repository import MongoManuscriptSessionRepository
from src.domains.agentverse.manuscripts.service import ManuscriptActor, ManuscriptService
from src.domains.agentverse.manuscripts.sessions import (
    SESSIONS_COLLECTION,
    SessionCorrupt,
    SessionRevisionConflict,
)
from src.domains.agentverse.manuscripts.tests.test_manuscript_runtime import (
    ACTOR,
    FULL,
    MANUSCRIPT,
    FakeCatalog,
)
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey
from src.domains.agentverse.tests.agents.test_creation_drafts_integration import (
    local_app,  # noqa: F401
    snapshot,
)
from src.domains.agentverse.tests.agents.test_creation_review_integration import creator
from src.domains.agentverse.tests.agents.test_recursive_genesis_integration import with_enki


@pytest_asyncio.fixture
async def mongo():
    if os.environ.get("OPS2_MONGO_INTEGRATION") != "1":
        pytest.skip("Set OPS2_MONGO_INTEGRATION=1 for real Mongo Manuscript tests")
    from src.base.infrastructure.db.mongoDB.isolation import isolated_mongo_repository

    async with isolated_mongo_repository("manuscripts") as infrastructure:
        yield infrastructure


def service(mongo) -> ManuscriptService:
    registry = ManuscriptRegistry(f"manuscripts-{id(mongo)}-{os.urandom(4).hex()}")
    register_builtin(registry)
    return ManuscriptService(
        registry=registry, repository=MongoManuscriptSessionRepository(mongo), catalog=FakeCatalog()
    )


@pytest.mark.asyncio
async def test_real_mongo_sessions_are_atomic_durable_and_tamper_evident(mongo):
    first = service(mongo)
    session, _ = await first.start(ACTOR, MANUSCRIPT, idempotency_key="integration-key-0001")
    results = await asyncio.gather(
        *[
            first.answer(
                ACTOR,
                session.session_id,
                expected_revision=1,
                answers={"purpose": f"Concurrent purpose number {n} for a test."},
                request_key=f"integration-req-{n:06d}",
            )
            for n in range(12)
        ],
        return_exceptions=True,
    )
    assert sum(not isinstance(r, BaseException) for r in results) == 1
    assert all(
        isinstance(r, SessionRevisionConflict) for r in results if isinstance(r, BaseException)
    )
    # A fresh service instance (as after a restart) continues the same session.
    second = service(mongo)
    current = (await second.status(ACTOR, session.session_id))["revision"]
    for index, answers in enumerate(FULL[1:]):
        state, _ = await second.answer(
            ACTOR,
            session.session_id,
            expected_revision=current,
            answers=answers,
            request_key=f"integration-step-{index:05d}",
        )
        current = state.revision
    done = await second.complete(
        ACTOR, session.session_id, expected_revision=current, request_key="integration-done-0001"
    )
    assert done.status == "completed" and done.artifact["creation"]["name"] == "Contract Summarizer"
    collection = await mongo.get_collection(SESSIONS_COLLECTION)
    await collection.update_one({"_id": session.session_id}, {"$set": {"stage": "identity"}})
    with pytest.raises(SessionCorrupt):
        await second.status(ACTOR, session.session_id)


@pytest.mark.asyncio
async def test_real_mongo_restart_recovers_from_an_interrupted_close(mongo):
    runtime = service(mongo)
    session, _ = await runtime.start(ACTOR, MANUSCRIPT, idempotency_key="integration-key-0002")
    repository = runtime._repository
    original = repository.replace

    async def crash(previous, following):
        raise RuntimeError("process stopped after inserting the successor")

    repository.replace = crash  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        await runtime.restart(ACTOR, session.session_id, request_key="integration-restart-01")
    repository.replace = original  # type: ignore[method-assign]
    successor = await runtime.restart(
        ACTOR, session.session_id, request_key="integration-restart-01"
    )
    assert (await runtime.status(ACTOR, session.session_id))["restarted_to"] == successor.session_id
    collection = await mongo.get_collection(SESSIONS_COLLECTION)
    assert await collection.count_documents({}) == 2


@pytest.mark.asyncio
async def test_bootstrap_manuscript_with_enki_permit_real_catalog_and_g33_review(local_app):  # noqa: F811
    s = await with_enki(local_app)
    manuscripts = s.app.state.manuscripts
    assert manuscripts._registry.frozen and MANUSCRIPT in manuscripts._registry
    client = await creator(s)
    try:
        session_id = (await client.get("/api/v2/agentverse/session")).json()["session_id"]
        before = await snapshot(s)
        token = client.headers["Authorization"].removeprefix("Bearer ")
        async with s.app.state.genesis_sessions.authenticated(token):
            principal = await s.av.utu_authority().resolve_agent_principal(
                AgentInstanceKey(TENANT, OWNER, ENKI, session_id)
            )
            actor = await ManuscriptActor.from_permit(s.av.utu_authority(), principal.permit)
        assert (actor.tenant_id, actor.subject_id, actor.agent_id) == (TENANT, OWNER, ENKI)
        assert isinstance(actor.requester, User) and actor.requester.id == OWNER
        session, _ = await manuscripts.start(
            actor, MANUSCRIPT, idempotency_key="enki-interview-0001"
        )
        steps = [
            {"purpose": "Summarize legal contracts for the sales team."},
            {"name": "Contract Summarizer"},
            {"agent_type": "chat", "role": "general", "soul": "empty_eva"},
            {"capabilities_reviewed": True},
            {"prompt": "Summarize contracts faithfully and flag risks.", "visibility": "private"},
            {"review_ready": True},
        ]
        _, invented = await manuscripts.answer(
            actor,
            session.session_id,
            expected_revision=1,
            answers={"role": "not_a_real_role"},
            request_key="enki-interview-bad-01",
        )
        assert invented.rejected == {"role": "reference.unavailable"}
        for index, answers in enumerate(steps):
            session, outcome = await manuscripts.answer(
                actor,
                session.session_id,
                expected_revision=session.revision,
                answers=answers,
                request_key=f"enki-interview-step-{index}",
            )
            assert outcome.status == "accepted", (answers, outcome)
        done = await manuscripts.complete(
            actor,
            session.session_id,
            expected_revision=session.revision,
            request_key="enki-interview-done-1",
        )
        # The artifact is exactly what G3.3 reviews; the Manuscript itself wrote nothing.
        review = await client.post(
            f"/api/v2/agentverse/agents/{ENKI}/creation-draft/validate", json=done.artifact
        )
        assert review.json()["eligibility"] == {"eligible": True, "reasons": []}, review.text
        after = await snapshot(s)
        assert after == before  # No Agent, approval, creation, log or runtime change.
        assert await s.mongo.db["coll_agent_drafts"].count_documents({}) == 0
        assert await s.mongo.db["coll_agent_draft_proposals"].count_documents({}) == 0
    finally:
        await client.aclose()
