"""REV-1 contracts: canonical definitions, chained revision records and read-only
baseline verification against authentic ``store.create`` evidence (no database)."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from src.base.operations.v2.admission import sha256_hex
from src.domains.agentverse.agents.definition_revisions.baseline import (
    baseline_revision,
    inspect_baseline,
    store_rule_v1_digest,
    verify_baseline,
)
from src.domains.agentverse.agents.definition_revisions.canonical import (
    CANONICAL_V1_FIELDS,
    RUNTIME_FIELDS,
    canonical_definition,
    definition_digest,
)
from src.domains.agentverse.agents.definition_revisions.models import (
    AgentDefinitionRevision,
    Predecessor,
    RevisionCorrelation,
    revision_id,
)
from src.domains.agentverse.entities.agent import Agent
from src.domains.agentverse.nataraja.plan import evaluate
from src.domains.agentverse.nataraja.tests.test_nataraja import (
    baseline,
    local_env,  # noqa: F401 - fixture re-export
    manifest,  # noqa: F401 - fixture re-export
)
from src.domains.agentverse.operations.admission import permit_provenance
from src.domains.agentverse.security.revocation import ExecutionPermit
from src.domains.agentverse.tests.tools.test_store_tool import (
    KEY,
    OWNER,
    TENANT,
    FakeMongoRepository,
    definition,
)
from src.domains.agentverse.tools.store_tool.models import StoreWriteContext
from src.domains.agentverse.tools.store_tool.persistence.agent_definition_repository import (
    AgentDefinitionRepository,
)
from src.domains.agentverse.tools.store_tool.store.agent_store import AgentDefinitionStore

T0 = datetime(2026, 10, 31, 12, 0, tzinfo=UTC)


async def created(**overrides: Any) -> dict[str, Any]:
    """A definition persisted by the real ``store.create`` logic (authentic evidence)."""
    repository = FakeMongoRepository()
    store = AgentDefinitionStore(
        persistence=AgentDefinitionRepository(repository), clock=lambda: T0
    )
    context = StoreWriteContext(
        provenance=permit_provenance(ExecutionPermit(KEY, ("p", "i", "0", "0")), "store.create"),
        verify=AsyncMock(),
    )
    await store.create(definition(**overrides), idempotency_key="rev-1", context=context)
    (document,) = repository.collection.documents
    return copy.deepcopy(document)


# --- Canonical definitions ------------------------------------------------------------------


def test_canonical_v1_field_set_is_pinned_to_the_agent_model():
    # Any Agent model change must introduce a new canonical schema version.
    assert set(Agent.model_fields) - RUNTIME_FIELDS == CANONICAL_V1_FIELDS


@pytest.mark.asyncio
async def test_canonical_digest_is_stable_and_ignores_storage_metadata():
    document = await created()
    agent_id = document["id"]
    canonical = canonical_definition(document)
    digest = definition_digest(TENANT, agent_id, canonical)
    # Independent of key order, timestamps, Mongo identity and the store record.
    shuffled = dict(reversed(list(document.items())))
    shuffled |= {"_id": "x", "updated_at": T0 + timedelta(days=1), "store": None}
    assert definition_digest(TENANT, agent_id, canonical_definition(shuffled)) == digest
    # Golden vectors: this exact definition's canonical v1 digest and its historical
    # store.create v1 request digest must never drift silently (a drift needs a new
    # canonical schema or historical rule, not an edited constant).
    assert agent_id == "69d4c749-0369-d3f8-9ad8-88170615503c"
    assert digest == "7fcdcab50d06188a6de4e282928d37c659d13f9aeb3f52234b510a2f53dfdbb8"
    assert document["store"]["request_digest"] == (
        "4b564ea1c2ebb608c5adb4e9b147549748efb2dfa6c049d476f104d7e41514dc"
    )
    assert definition_digest("other-tenant", agent_id, canonical) != digest
    changed = canonical_definition({**document, "prompt": "Changed."})
    assert definition_digest(TENANT, agent_id, changed) != digest


# --- Revision records -----------------------------------------------------------------------


async def baseline_record(document: dict[str, Any] | None = None) -> AgentDefinitionRevision:
    document = document or await created()
    verification = verify_baseline(TENANT, document["id"], [document])
    return baseline_revision(verification, document, recorded_at=T0)


def successor(previous: AgentDefinitionRevision, **changes: Any) -> AgentDefinitionRevision:
    return AgentDefinitionRevision.build(
        tenant_id=previous.tenant_id,
        agent_id=previous.agent_id,
        revision=previous.revision + 1,
        origin="revision",
        predecessor=Predecessor(
            revision=previous.revision, revision_digest=previous.revision_digest
        ),
        definition={**previous.definition, **changes},
        correlation=RevisionCorrelation(operation_id="op-1"),
        recorded_at=T0,
    )


@pytest.mark.asyncio
async def test_records_chain_exact_predecessors_and_bind_every_fact():
    first = await baseline_record()
    assert (first.revision, first.origin, first.predecessor) == (1, "baseline", None)
    assert first.baseline.verification.status == "verified"
    second = successor(first, prompt="Second.")
    assert second.predecessor.revision_digest == first.revision_digest
    assert second.revision_digest != first.revision_digest
    third = successor(second, prompt="Third.")
    assert third.predecessor == Predecessor(revision=2, revision_digest=second.revision_digest)
    # Insertion time is storage metadata, outside digests and content identity.
    assert successor(first, prompt="Second.").content() == second.content()
    later = AgentDefinitionRevision.model_validate(
        {**second.model_dump(), "recorded_at": T0 + timedelta(hours=1)}
    )
    assert later.revision_digest == second.revision_digest


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tamper",
    [
        {"revision": 3},
        {"definition_digest": "0" * 64},
        {"revision_digest": "0" * 64},
        {"tenant_id": "other-tenant"},
        {"origin": "baseline"},
        {"predecessor": None},
        {"predecessor": {"revision": 1, "revision_digest": "f" * 64}},
        {"canonical_schema": "caos.agent_definition@2"},
        {"permit": "secret"},
    ],
)
async def test_tampered_records_are_rejected(tamper):
    second = successor(await baseline_record(), prompt="Second.")
    with pytest.raises(ValidationError):
        AgentDefinitionRevision.model_validate({**second.model_dump(), **tamper})


@pytest.mark.asyncio
async def test_definitions_must_be_complete_canonical_and_self_identified():
    first = await baseline_record()
    for definition_ in (
        {k: v for k, v in first.definition.items() if k != "prompt"},
        {**first.definition, "extra": 1},
        {**first.definition, "id": "another-agent"},
        {**first.definition, "tenant_id": "another-tenant"},
    ):
        with pytest.raises(ValidationError):
            AgentDefinitionRevision.build(
                tenant_id=first.tenant_id,
                agent_id=first.agent_id,
                revision=2,
                origin="revision",
                predecessor=Predecessor(revision=1, revision_digest=first.revision_digest),
                definition=definition_,
                recorded_at=T0,
            )


def test_revision_identity_is_tenant_and_agent_qualified():
    ids = {
        revision_id("t", "a", 1),
        revision_id("t", "a", 2),
        revision_id("t", "b", 1),
        revision_id("u", "a", 1),
        revision_id("t:a", "1", 1),
    }
    assert len(ids) == 5


# --- Baseline verification ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_unchanged_store_created_definition_is_verified():
    document = await created()
    result = verify_baseline(TENANT, document["id"], [document])
    assert result.status == "verified" and result.codes == ()
    assert result.recorded_request_digest == result.recomputed_request_digest
    assert result.creation_evidence == "available" and result.duplicate_count == 1
    assert result.modified_after_creation is False and result.revision_ready
    assert store_rule_v1_digest(document) == document["store"]["request_digest"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "case,status,code",
    [
        ("modified", "conflicted", "definition.modified_after_creation"),
        ("silent_change", "unverified", "definition.canonicalization_mismatch"),
        ("touched", "verified", "definition.touched_after_creation"),
        ("unknown_field", "unverified", "definition.unattributed_fields"),
        ("invalid", "unverified", "definition.invalid_under_current_model"),
        ("other_creator", "conflicted", "creation_evidence.identity_mismatch"),
        ("marker", "conflicted", "projection.unexpected_marker"),
        ("no_store", "unavailable", "creation_evidence.missing"),
        ("seed", "unavailable", "creation_evidence.seed"),
        ("store_v2", "unavailable", "creation_evidence.unsupported"),
        ("bad_store", "unavailable", "creation_evidence.malformed"),
        ("other_tenant", "unavailable", "definition.missing"),
    ],
)
async def test_uncertain_or_contradicting_evidence_is_never_verified(case, status, code):
    document = await created()
    later = T0 + timedelta(minutes=5)
    if case == "modified":  # a legacy write changed content and advanced updated_at
        document |= {"prompt": "Patched.", "updated_at": later}
    elif case == "silent_change":  # content differs with no write evidence
        document["prompt"] = "Patched."
    elif case == "touched":  # a write that left the content identical
        document["updated_at"] = later
    elif case == "unknown_field":
        document["nickname"] = "unattributed"
    elif case == "invalid":
        document["visibility"] = "secret"
    elif case == "other_creator":
        document["creator"] = "someone-else"
    elif case == "marker":
        document["definition_revision"] = {"revision": 2}
    elif case == "no_store":
        document.pop("store")
    elif case == "seed":
        document.pop("store")
        document["genesis_local_seed"] = True
    elif case == "store_v2":
        document["store"]["version"] = 2
    elif case == "bad_store":
        document["store"]["request_digest"] = "not-a-digest"
    tenant = "other-tenant" if case == "other_tenant" else TENANT
    result = verify_baseline(tenant, document["id"], [document])
    assert (result.status, code in result.codes) == (status, True), result
    if status != "verified":
        assert not result.revision_ready
        with pytest.raises(ValueError):
            baseline_revision(result, document, recorded_at=T0)


@pytest.mark.asyncio
async def test_historical_model_differences_stay_unverified():
    # Evidence produced by an older Agent model that had no ``gui_components`` field:
    # the current model adds a default, so the historical digest cannot be reproduced.
    document = await created()
    historical = Agent.model_validate(
        {k: v for k, v in document.items() if k not in ("_id", "store")}
    ).model_dump(mode="json")
    historical.pop("gui_components")
    document["store"]["request_digest"] = sha256_hex(
        {"resource_type": "agent", "definition": historical}
    )
    document.pop("gui_components")
    result = verify_baseline(TENANT, document["id"], [document])
    assert (result.status, result.codes) == (
        "unverified",
        ("definition.canonicalization_mismatch",),
    )
    # The historical digest is reported unchanged, never rewritten.
    assert result.recorded_request_digest == document["store"]["request_digest"]


@pytest.mark.asyncio
async def test_duplicate_identities_conflict_across_tenants():
    document = await created()
    twin = {**copy.deepcopy(document), "tenant_id": "other-tenant"}
    result = verify_baseline(TENANT, document["id"], [document, twin])
    assert (result.status, result.codes, result.duplicate_count) == (
        "conflicted",
        ("identity.duplicate",),
        2,
    )


class Reader:
    def __init__(self, documents=None, error=None):
        self.documents, self.error, self.calls = documents or [], error, []

    async def find(self, collection, query, projection=None):
        self.calls.append((collection, query, projection))
        if self.error:
            raise self.error
        return [d for d in self.documents if d.get("id") == query["id"]]


@pytest.mark.asyncio
async def test_inspection_reads_once_and_reports_unavailable_stores():
    document = await created()
    reader = Reader([document])
    assert (await inspect_baseline(reader, TENANT, document["id"])).status == "verified"
    assert reader.calls == [("coll_agents", {"id": document["id"]}, {"_id": 0})]
    broken = await inspect_baseline(Reader(error=RuntimeError("down")), TENANT, "x")
    assert (broken.status, broken.codes) == ("unavailable", ("store.unavailable",))


@pytest.mark.asyncio
async def test_summaries_carry_no_definition_content():
    document = await created(prompt="PRIVATE_SENTINEL")
    summary = verify_baseline(TENANT, document["id"], [document]).summary()
    assert "PRIVATE_SENTINEL" not in repr(summary) and OWNER not in repr(summary)
    assert set(summary) == {
        "agent_id",
        "status",
        "codes",
        "creation_evidence",
        "rule",
        "duplicate_count",
        "current_definition_digest",
        "recorded_request_digest",
        "recomputed_request_digest",
        "modified_after_creation",
        "revision_ready",
    }


def test_nataraja_reports_each_baseline_outcome_read_only(local_env, manifest):  # noqa: F811
    summaries = [
        {"agent_id": agent, "name": agent.title(), "status": status, "codes": codes}
        for agent, status, codes in (
            ("enki", "verified", []),
            ("thoth", "unverified", ["definition.canonicalization_mismatch"]),
            ("nabu", "conflicted", ["identity.duplicate"]),
            ("seed", "unavailable", ["creation_evidence.seed"]),
        )
    ]
    report = evaluate(manifest, baseline(local_env, definition_baselines=summaries), mode="inspect")
    rows = {r["id"]: r for r in report["resources"] if r["kind"] == "definition_baseline"}
    assert {k: v["status"] for k, v in rows.items()} == {
        "baseline:enki": "passed",
        "baseline:thoth": "warning",
        "baseline:nabu": "failed",
        "baseline:seed": "deferred",
    }
    findings = {
        f["resource"]: (f["code"], f["codes"])
        for f in report["findings"]
        if f["code"].startswith("definition_baseline.")
    }
    assert findings == {
        "baseline:thoth": (
            "definition_baseline.unverified",
            ["definition.canonicalization_mismatch"],
        ),
        "baseline:nabu": ("definition_baseline.conflicted", ["identity.duplicate"]),
    }
    assert report["reconciliation_plan"] == []  # inspection plans and writes nothing
