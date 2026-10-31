"""AGENT-CTX-0.1 contracts: exact frozen registries, typed slots, record integrity,
verified actor and the requester projection (no database)."""

import dataclasses
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.activities.models import (
    ActivationIntent,
    ActivityContext,
    ActivityContractUnavailable,
    ActivityOperationConflict,
    ActivityRef,
    ActivityScopeHead,
    ActorScope,
    AppliedOperation,
    Causation,
    OwnerRef,
    ResourceRef,
)
from src.domains.agentverse.activities.registry import (
    ActivityContractRegistry,
    RegistryFrozen,
    ResolvedResource,
    ResourceResolverRegistry,
    ResourceSlot,
    SlotLink,
)
from src.domains.agentverse.activities.service import ActivityActor, view
from src.domains.agentverse.agents.creation_drafts.activity import AGENT_CREATION_INTERVIEW
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey
from src.domains.agentverse.security.revocation import ExecutionPermit

T0 = datetime(2026, 10, 31, 9, 0, tzinfo=UTC)
CAUSE = Causation(event_id="turn-1", fence=1, operation_id="op-start-0000000001")


class Resolver:
    def __init__(self, kind):
        self.kind = kind

    async def resolve(self, actor, resource_id):
        return ResolvedResource(self.kind, resource_id, 1, "active")


def registries(*kinds):
    resolvers = ResourceResolverRegistry()
    for kind in kinds or ("manuscript.session", "agent.creation_draft", "agent.draft.proposal"):
        resolvers.register(Resolver(kind))
    return resolvers, ActivityContractRegistry(resolvers, frozenset({"conversation.v2"}))


def contract(**changes):
    return dataclasses.replace(AGENT_CREATION_INTERVIEW, **changes)


def test_the_creation_interview_contract_is_typed_exact_and_agent_neutral():
    _, contracts = registries()
    contracts.register(AGENT_CREATION_INTERVIEW)
    contracts.freeze()
    assert contracts.keys() == ["agent.creation_interview@1.0.0"]
    found = contracts.resolve(
        "agent.creation_interview@1.0.0", AGENT_CREATION_INTERVIEW.fingerprint()
    )
    assert [(s.name, s.kind, s.required, s.update) for s in found.slots] == [
        ("manuscript_session", "manuscript.session", True, "successor"),
        ("draft", "agent.creation_draft", True, "immutable"),
        ("proposal", "agent.draft.proposal", False, "replace"),
    ]
    assert "enki" not in repr(found).lower()
    for key, fingerprint in (
        ("agent.creation_interview@1.0", None),
        ("agent.creation_interview@1.0.1", None),
        ("agent.creation_interview", None),
        ("agent.creation_interview@1.0.0", "0" * 64),
    ):
        with pytest.raises(ActivityContractUnavailable):
            contracts.resolve(key, fingerprint)
    with pytest.raises(RegistryFrozen):
        contracts.register(contract(version="2.0.0"))


@pytest.mark.parametrize(
    "bad",
    [
        {"owner_kinds": frozenset({"workflow.run"})},  # no such owner kind yet
        {"owner_kinds": frozenset()},
        {"slots": ()},
        {"version": "latest"},
        {"title": ""},
        {
            "slots": (
                ResourceSlot(
                    "x", "unregistered.kind", True, "immutable", frozenset({"a"}), frozenset({"a"})
                ),
            )
        },
        {
            "slots": (
                ResourceSlot(
                    "x", "manuscript.session", True, "immutable", frozenset(), frozenset({"a"})
                ),
            )
        },
        {"slots": AGENT_CREATION_INTERVIEW.slots[:1] * 2},
        {"links": (SlotLink("missing", "draft", "draft"),)},
    ],
)
def test_contract_registration_rejects_untyped_or_unresolvable_declarations(bad):
    _, contracts = registries()
    with pytest.raises(ValueError):
        contracts.register(contract(**bad))


def test_registries_reject_conflicts_and_freeze():
    resolvers, contracts = registries()
    contracts.register(AGENT_CREATION_INTERVIEW)
    with pytest.raises(ValueError):
        contracts.register(contract(title="Changed under the same key"))
    with pytest.raises(ValueError):
        resolvers.register(Resolver("manuscript.session"))  # A second provider for a kind.
    with pytest.raises(ValueError):
        resolvers.register(Resolver("no_dot"))
    resolvers.freeze()
    with pytest.raises(RegistryFrozen):
        resolvers.register(Resolver("other.kind"))


def record(**changes):
    refs = {"draft": ResourceRef(kind="agent.creation_draft", id="d", observed_revision=1)}
    fields = {
        "context_id": "c1",
        "tenant_id": "tenant",
        "scope_key": "a" * 64,
        "actor": ActorScope(subject_id="owner", principal_id="agent", principal_kind="agent"),
        "owner": OwnerRef(kind="conversation.v2", id="conv"),
        "activity": ActivityRef(key="agent.creation_interview@1.0.0", fingerprint="b" * 64),
        "generation": 1,
        "status": "active",
        "references": refs,
        "revision": 1,
        "origin": "start",
        "start_digest": "c" * 64,
        "started_references": refs,
        "created_by": CAUSE,
        "last_owner": CAUSE,
        "applied": [AppliedOperation(operation_id=CAUSE.operation_id, digest="c" * 64, revision=1)],
        "created_at": T0,
        "updated_at": T0,
        **changes,
    }
    return ActivityContext.build(**fields)


def test_records_are_digest_bound_typed_and_reference_only():
    context = record()
    assert context.contract == "caos.activity_context"
    with pytest.raises(ValidationError):
        ActivityContext.model_validate({**context.model_dump(), "revision": 2})
    for forbidden in ("messages", "answers", "content", "permit", "token", "prompt", "model"):
        with pytest.raises(ValidationError):
            record(**{forbidden: "x"})
    with pytest.raises(ValidationError):
        record(references={"draft": {"kind": "agent.creation_draft", "id": "d", "extra": 1}})
    with pytest.raises(ValidationError):
        record(actor=ActorScope(subject_id="o", principal_id="g", principal_kind="system_citizen"))
    for changes in (
        {"status": "closed"},
        {"closed_reason": "completed"},
        {"generation": 2},
        {"previous_context_id": "c0"},
    ):
        with pytest.raises(ValidationError):
            record(**changes)
    assert context.replay(CAUSE.operation_id, "c" * 64)
    with pytest.raises(ActivityOperationConflict):
        context.replay(CAUSE.operation_id, "d" * 64)


def test_a_head_intent_is_always_the_active_generation():
    successor = record()
    base = {
        "scope_key": "a" * 64,
        "tenant_id": "tenant",
        "actor": successor.actor,
        "owner": successor.owner,
        "revision": 1,
        "updated_at": T0,
    }
    intent = ActivationIntent(operation_id=CAUSE.operation_id, digest="c" * 64, successor=successor)
    ActivityScopeHead.build(**base, generation=1, active_context_id="c1", intent=intent)
    for generation, active in ((1, "other"), (2, "c1"), (1, None)):
        with pytest.raises(ValidationError):
            ActivityScopeHead.build(
                **base, generation=generation, active_context_id=active, intent=intent
            )


def test_the_requester_projection_omits_internals():
    projected = view(record()).model_dump()
    assert set(projected) == {
        "contract",
        "context_id",
        "activity",
        "owner_kind",
        "owner_id",
        "generation",
        "status",
        "closed_reason",
        "previous_context_id",
        "revision",
        "references",
    }


@pytest.mark.asyncio
async def test_the_actor_comes_only_from_a_verified_session_permit():
    key = AgentInstanceKey("tenant", "owner", "agent", "session")
    authority = SimpleNamespace(
        check_permit=AsyncMock(),
        resource_requester=AsyncMock(return_value=SimpleNamespace(id="owner")),
        grants_for=Mock(return_value=SimpleNamespace(invoke=frozenset({"chat.response"}))),
    )
    principal = SimpleNamespace(permit=ExecutionPermit(key=key, versions=("p:v1",)))
    actor = await ActivityActor.from_principal(authority, principal)
    assert (actor.tenant_id, actor.subject_id, actor.principal_id, actor.session_id) == (
        "tenant",
        "owner",
        "agent",
        "session",
    )
    assert actor.grants == frozenset({"chat.response"})
    with pytest.raises(EventPermissionError):
        await ActivityActor.from_principal(
            authority,
            SimpleNamespace(permit=ExecutionPermit(key=key, versions=("p",), origin="bootstrap")),
        )
    authority.resource_requester.return_value = SimpleNamespace(id="someone-else")
    with pytest.raises(EventPermissionError):
        await ActivityActor.from_principal(authority, principal)
