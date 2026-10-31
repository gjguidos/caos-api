"""REV-1 on real (disposable) MongoDB: authentic ``store.create`` evidence, read-only
baseline inspection (Nataraja's reader), insert-only revisions, competing successors and
isolation. Nothing writes ``coll_agents`` except the test's own Store creation.

Requires ``OPS2_MONGO_INTEGRATION=1``.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import pathlib
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio

from src.domains.agentverse.agents.definition_revisions.baseline import (
    baseline_revision,
    inspect_baseline,
    verify_baseline,
)
from src.domains.agentverse.agents.definition_revisions.models import (
    REVISIONS_COLLECTION,
    AgentDefinitionRevision,
    Predecessor,
    RevisionConflict,
    RevisionCorrelation,
    RevisionPredecessorMismatch,
)
from src.domains.agentverse.agents.definition_revisions.repository import (
    MongoAgentDefinitionRevisionRepository,
)
from src.domains.agentverse.nataraja.observe import MotorReadOnlyMongo, _definition_baselines
from src.domains.agentverse.operations.admission import permit_provenance
from src.domains.agentverse.security.revocation import ExecutionPermit
from src.domains.agentverse.services.db_service import DBService
from src.domains.agentverse.tests.tools.test_store_tool import KEY, TENANT, definition
from src.domains.agentverse.tools.store_tool.models import StoreWriteContext
from src.domains.agentverse.tools.store_tool.persistence.agent_definition_repository import (
    AgentDefinitionRepository,
)
from src.domains.agentverse.tools.store_tool.store.agent_store import AgentDefinitionStore

T0 = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)
SRC = pathlib.Path(__file__).resolve().parents[5]  # .../src


@pytest_asyncio.fixture
async def env():
    if os.environ.get("OPS2_MONGO_INTEGRATION") != "1":
        pytest.skip("Set OPS2_MONGO_INTEGRATION=1 for real Mongo revision tests")
    from src.base.infrastructure.db.mongoDB.isolation import isolated_mongo_repository

    async with isolated_mongo_repository("revisions") as mongo:
        store = AgentDefinitionStore(persistence=AgentDefinitionRepository(mongo))
        context = StoreWriteContext(
            provenance=permit_provenance(
                ExecutionPermit(KEY, ("p", "i", "0", "0")), "store.create"
            ),
            verify=AsyncMock(),
        )
        created = await store.create(definition(), idempotency_key="rev-1", context=context)
        agents = await mongo.get_collection(DBService.AGENTS_COL)
        reader = MotorReadOnlyMongo(agents.database)
        yield SimpleNamespace(
            mongo=mongo,
            agents=agents,
            reader=reader,
            agent_id=created.resource_id,
            revisions=MongoAgentDefinitionRevisionRepository(mongo),
        )


async def snapshot(e) -> list[dict]:
    rows = await e.agents.find({}, {"_id": 0}).to_list(None)
    return json.loads(
        json.dumps(sorted(rows, key=lambda r: (r["id"], r["tenant_id"])), default=str)
    )


async def baseline(e) -> AgentDefinitionRevision:
    (document,) = await e.reader.find(DBService.AGENTS_COL, {"id": e.agent_id}, {"_id": 0})
    verification = verify_baseline(TENANT, e.agent_id, [document])
    return baseline_revision(verification, document, recorded_at=T0)


def successor(previous: AgentDefinitionRevision, prompt: str, operation: str = "op-1"):
    return AgentDefinitionRevision.build(
        tenant_id=previous.tenant_id,
        agent_id=previous.agent_id,
        revision=previous.revision + 1,
        origin="revision",
        predecessor=Predecessor(
            revision=previous.revision, revision_digest=previous.revision_digest
        ),
        definition={**previous.definition, "prompt": prompt},
        correlation=RevisionCorrelation(operation_id=operation),
        recorded_at=T0,
    )


@pytest.mark.asyncio
async def test_a_store_created_definition_verifies_through_the_read_only_reader(env):
    e = env
    before = await snapshot(e)
    verification = await inspect_baseline(e.reader, TENANT, e.agent_id)
    assert verification.status == "verified", verification
    # The plain (naive-datetime) driver read verifies identically.
    rows = await e.agents.find({"id": e.agent_id}, {"_id": 0}).to_list(None)
    assert verify_baseline(TENANT, e.agent_id, rows).status == "verified"
    # Nataraja's observation: safe summaries only, nothing written.
    (summary,) = await _definition_baselines(e.reader, TENANT)
    assert (summary["agent_id"], summary["status"], summary["revision_ready"]) == (
        e.agent_id,
        "verified",
        True,
    )
    assert "You are Adam." not in json.dumps(summary)
    assert await snapshot(e) == before
    names = await e.agents.database.list_collection_names()
    assert REVISIONS_COLLECTION not in names  # inspection never creates revision storage


@pytest.mark.asyncio
async def test_duplicate_agent_identities_are_conflicted(env):
    e = env
    (row,) = await e.agents.find({"id": e.agent_id}, {"_id": 0}).to_list(None)
    # The Store's unique idempotency index blocks a second holder with the same Store
    # scope, but coll_agents has no unique ``id`` index: a holder without a Store record
    # (a V1-style document) can still carry the same ID.
    twin = {k: v for k, v in copy.deepcopy(row).items() if k != "store"}
    await e.agents.insert_one({**twin, "tenant_id": "other-tenant"})
    verification = await inspect_baseline(e.reader, TENANT, e.agent_id)
    assert (verification.status, verification.duplicate_count) == ("conflicted", 2)
    (summary,) = await _definition_baselines(e.reader, TENANT)
    assert summary["codes"] == ["identity.duplicate"] and summary["revision_ready"] is False


@pytest.mark.asyncio
async def test_a_legacy_write_after_creation_is_conflicted(env):
    e = env
    # Simulates a pre-AGENT-DEF-SEC-0.1 V1 PATCH: content and ``updated_at`` both change.
    await e.agents.update_one(
        {"id": e.agent_id},
        {"$set": {"prompt": "Patched.", "updated_at": datetime(2026, 11, 1, tzinfo=UTC)}},
    )
    verification = await inspect_baseline(e.reader, TENANT, e.agent_id)
    assert (verification.status, verification.codes) == (
        "conflicted",
        ("definition.modified_after_creation",),
    )


@pytest.mark.asyncio
async def test_revisions_are_inserted_once_and_identical_retries_are_idempotent(env):
    e = env
    before = await snapshot(e)
    first = await baseline(e)
    stored, created = await e.revisions.insert(first)
    assert created and stored == first
    again, created = await e.revisions.insert(
        AgentDefinitionRevision.model_validate(
            {**first.model_dump(), "recorded_at": datetime.now(UTC)}
        )
    )
    assert not created and again.content() == first.content()
    assert await e.revisions.get(TENANT, e.agent_id, 1) == first
    # Revision storage never touches the Agent projection or its creation record.
    assert await snapshot(e) == before
    collection = await e.mongo.get_collection(REVISIONS_COLLECTION)
    assert list(await collection.index_information()) == ["_id_"]  # no index installed


@pytest.mark.asyncio
async def test_competing_successors_for_one_revision_have_exactly_one_winner(env):
    e = env
    first, _ = await e.revisions.insert(await baseline(e))
    candidates = [successor(first, f"Candidate {i}.", operation=f"op-{i}") for i in range(8)]
    outcomes = await asyncio.gather(
        *(e.revisions.insert(c) for c in candidates), return_exceptions=True
    )
    winners = [o for o in outcomes if isinstance(o, tuple)]
    assert len(winners) == 1 and winners[0][1] is True
    assert all(isinstance(o, RevisionConflict) for o in outcomes if not isinstance(o, tuple))
    stored = await e.revisions.get(TENANT, e.agent_id, 2)
    assert stored.content() == winners[0][0].content()
    # The selected successor chains exactly; a stale or skipped predecessor is refused.
    third = successor(stored, "Third.")
    assert (await e.revisions.insert(third))[1]
    # A successor of a losing candidate names an unstored predecessor: refused.
    loser = next(c for c in candidates if c.content() != stored.content())
    with pytest.raises(RevisionPredecessorMismatch):
        await e.revisions.insert(successor(loser, "Fork."))
    skipped = AgentDefinitionRevision.build(
        tenant_id=TENANT,
        agent_id=e.agent_id,
        revision=9,
        origin="revision",
        predecessor=Predecessor(revision=8, revision_digest="a" * 64),
        definition=first.definition,
        recorded_at=T0,
    )
    with pytest.raises(RevisionPredecessorMismatch):
        await e.revisions.insert(skipped)
    assert [r.revision for r in await e.revisions.chain(TENANT, e.agent_id)] == [1, 2, 3]


@pytest.mark.asyncio
async def test_histories_are_agent_and_tenant_isolated(env):
    e = env
    first, _ = await e.revisions.insert(await baseline(e))
    assert await e.revisions.get("other-tenant", e.agent_id, 1) is None
    assert await e.revisions.get(TENANT, "other-agent", 1) is None
    assert await e.revisions.chain("other-tenant", e.agent_id) == []
    assert (
        first.revision_id
        != AgentDefinitionRevision.build(
            **{
                **first.model_dump(exclude={"revision_id", "definition_digest", "revision_digest"}),
                "agent_id": "other-agent",
                "definition": {**first.definition, "id": "other-agent"},
            }
        ).revision_id
    )


def test_revision_wiring_is_limited_to_the_reviewed_modules():
    package = "definition_revisions"
    users = {
        str(path.relative_to(SRC))
        for path in (SRC / "domains").rglob("*.py")
        if package in path.read_text()
        and "/definition_revisions/" not in str(path)
        and not path.name.startswith("test_")
    }
    # REV-1: read-only Nataraja inspection. REV-2: DI repositories, policy parsing,
    # trusted startup construction and the human authoring/review routes. Nothing else.
    assert users == {
        "domains/agentverse/nataraja/observe.py",
        # REV-5: the canonical digest that binds a turn to its definition.
        "domains/agentverse/agents/conversational_composition_v2.py",
        "domains/agentverse/dependencies/di_container.py",
        "domains/agentverse/dependencies/genesis_security.py",
        "domains/agentverse/dependencies/genesis_bootstrap.py",
        "domains/agentverse/interfaces/api/v2/agent_definition_revisions.py",
    }
    policy = json.loads((SRC.parent / "config" / "genesis.local.json").read_text())
    # REV-2 declares revision_gateways; still no revision grant, signal or trusted line.
    assert not [s for g in policy["grants"] for s in g["invoke"] if "revis" in s]
    assert policy["trusted_lines"] == []
