"""G3.4 scope boundary and ``store.create.approved`` independent verification.

Real UTU/CPBX/validator/composer and approval service from the G3.3 stack; Store
persistence is in memory (real Mongo coverage: test_agent_creation_integration.py).
"""

from typing import Any

import pytest

from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.agents.creation_review.creation import (
    CREATION_SCOPE,
    AgentCreationRecord,
    ApprovedStoreCapability,
    register_store_create_approved,
    target_agent_id,
)
from src.domains.agentverse.ncsc.models import SynapticGrants
from src.domains.agentverse.ncsc.tests.test_scoped_authority import KEY, STORE, VALIDATE
from src.domains.agentverse.security.utu import AuthorizedAgentPrincipal
from src.domains.agentverse.security.validation_gateway import VALIDATION_SCOPE
from src.domains.agentverse.tests.agents.test_creation_review import (  # noqa: F401
    allow,
    approval,
    review,
    reviewed,
)
from src.domains.agentverse.tests.agents.test_validation_gateway import stack  # noqa: F401
from src.domains.agentverse.tools.store_tool import StoreTool
from src.domains.agentverse.tools.store_tool.store.agent_store import AgentDefinitionStore

APPROVED = "store.create.approved"


class Persistence:
    def __init__(self) -> None:
        self.documents: dict[str, dict[str, Any]] = {}

    async def ensure_indexes(self) -> None:
        return None

    async def insert_once(self, document: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        scope = document["store"]["idempotency_scope"]
        for existing in self.documents.values():
            if existing["store"]["idempotency_scope"] == scope:
                return existing, False
        self.documents[document["id"]] = document
        return document, True

    async def find_by_id(self, agent_id: str) -> dict[str, Any] | None:
        return self.documents.get(agent_id)


@pytest.fixture
def creation(review):  # noqa: F811
    s = review
    grants = s.env.policy._grants
    grants[(KEY.tenant_id, KEY.agent_id)] = SynapticGrants(
        {VALIDATE.signal, STORE.signal, APPROVED}
    )
    s.persistence = Persistence()
    tool = StoreTool(
        stores={"agent": AgentDefinitionStore(persistence=s.persistence)},
        authority=s.env.authority,
    )
    register_store_create_approved(
        s.env.registry, ApprovedStoreCapability(tool=tool, approvals=s.approvals)
    )
    return s


async def approved_record(s, *, consume: str | None = "creation-1"):
    digest = (await reviewed(s)).digest
    record = await s.approvals.approve(
        s.env.session, "source", approval(digest), authorize_creator=allow
    )
    if consume:
        record = await s.approvals.consume(record, consume)
    return record


async def client_for(s, scope=CREATION_SCOPE):
    principal = await s.env.authority.resolve_agent_principal(KEY)
    if scope is not None:
        principal = await s.env.authority.attenuate(principal, scope)
    return s.env.exchange.bind(principal)


def body(record, **override):
    return {
        "resource_type": "agent",
        "approval_id": record.approval_id,
        "review_digest": record.review_digest,
        "creation_id": "creation-1",
        "payload": record.snapshot["candidate"],
        **override,
    }


@pytest.mark.asyncio
async def test_only_the_creation_scope_can_write_and_it_reaches_nothing_else(creation):
    s = creation
    record = await approved_record(s)
    # An unscoped Genesis permit holds the root grant but is refused by Store itself.
    with pytest.raises(EventPermissionError):
        await (await client_for(s, None)).request(APPROVED, body(record))
    # The validation-only scope cannot reach Store at all.
    with pytest.raises(EventPermissionError):
        await (await client_for(s, VALIDATION_SCOPE)).request(APPROVED, body(record))
    scoped = await client_for(s)
    for signal in (VALIDATE.signal, STORE.signal, "store.create", "genesis.thoth.request"):
        with pytest.raises(EventPermissionError):
            await scoped.request(signal, {})
    # A rebound child of the creation scope cannot start a fresh root write either.
    principal = AuthorizedAgentPrincipal(KEY.agent_id, KEY.tenant_id, scoped.execution_permit)
    assert s.env.exchange.bind(principal).execution_permit.scope == CREATION_SCOPE
    assert s.persistence.documents == {}


@pytest.mark.asyncio
async def test_store_independently_verifies_the_effective_consumed_approval(creation):
    s = creation
    record = await approved_record(s)
    client = await client_for(s)
    for override, code in (
        ({"creation_id": "another-creation"}, "approval.mismatch"),
        ({"review_digest": "0" * 64}, "approval.mismatch"),
        ({"payload": {**record.snapshot["candidate"], "prompt": "Injected."}}, "approval.mismatch"),
        ({"approval_id": "unknown"}, "approval.not_found"),
        ({"payload": {"name": "x"}, "extra": True}, "store.invalid_request"),
    ):
        result = await client.request(APPROVED, body(record, **override))
        assert (result["success"], result["meta"]["code"]) == (False, code)
    # Evidence missing: an approved record is not effective without it.
    s.audit.mongo.collection.documents.clear()
    result = await client.request(APPROVED, body(record))
    assert result["meta"]["code"] == "approval.not_effective"
    assert s.persistence.documents == {}


@pytest.mark.asyncio
async def test_unconsumed_approval_cannot_be_written(creation):
    s = creation
    record = await approved_record(s, consume=None)
    result = await (await client_for(s)).request(APPROVED, body(record))
    assert result["meta"]["code"] == "approval.mismatch"
    assert s.persistence.documents == {}


@pytest.mark.asyncio
async def test_one_approval_writes_its_exact_candidate_at_one_stable_identity(creation):
    s = creation
    record = await approved_record(s)
    client = await client_for(s)
    first = await client.request(APPROVED, body(record), version="1.0.0")
    assert first["success"] is True and first["result"]["created"] is True
    agent_id = first["result"]["resource_id"]
    assert agent_id == target_agent_id(KEY.tenant_id, KEY.user_id, KEY.agent_id, record.approval_id)
    again = await client.request(APPROVED, body(record))
    assert (again["result"]["resource_id"], again["result"]["created"]) == (agent_id, False)
    (stored,) = s.persistence.documents.values()
    assert {k: stored[k] for k in record.snapshot["candidate"]} == record.snapshot["candidate"]
    assert (stored["tenant_id"], stored["creator"], stored["ownership"]) == (
        KEY.tenant_id,
        KEY.user_id,
        "tenant",
    )
    # Pinned version: the scope refuses any other provider version outright.
    with pytest.raises(EventPermissionError):
        await client.request(APPROVED, body(record), version="2.0.0")


def test_creation_record_requires_evidence_for_every_state():
    base = {
        "schema_version": 1,
        "creation_id": "c",
        "tenant_id": KEY.tenant_id,
        "subject_id": KEY.user_id,
        "source_agent_id": "source",
        "executor_id": KEY.agent_id,
        "approval_id": "a",
        "review_digest": "a" * 64,
        "request_digest": "b" * 64,
        "operation_id": "00000000-0000-4000-8000-000000000009",
        "target_agent_id": "t",
        "state": "accepted",
        "created_at": "2026-10-30T09:00:00Z",
        "updated_at": "2026-10-30T09:00:00Z",
        "authorization": {
            "authority": "utu",
            "tenant_id": KEY.tenant_id,
            "principal_id": KEY.agent_id,
            "subject_id": KEY.user_id,
            "policy_version": "p",
            "evidence_digest": "c" * 64,
        },
    }
    AgentCreationRecord.model_validate(base)
    for invalid in (
        {"state": "provisioning"},  # dispatched without authorization evidence
        {"state": "completed", "authorized_event_id": "e"},  # no Store evidence
        {"state": "failed"},  # failed without codes
        {"agent_id": "other"},  # identity differs from the stable target
        {"authorization": {**base["authorization"], "subject_id": "intruder"}},
    ):
        with pytest.raises(ValueError):
            AgentCreationRecord.model_validate({**base, **invalid})
