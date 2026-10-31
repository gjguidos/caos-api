"""MIG-FIX-0.1: activity consent is explicit and fails closed.

Legacy Conversation compatibility declares exact activities (Enki: the creation
interview; Thoth and Nabu: none); a contract registered later is never implied; an
absent declaration source or owner consent denies new generations; consent never
replaces the owner's UTU authorization; existing contexts stay recoverable.
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.activities.models import (
    ActivityContractUnavailable,
    ActivityNotFound,
)
from src.domains.agentverse.activities.owners import TURN_SIGNAL, ConversationOwner
from src.domains.agentverse.activities.registry import (
    ActivityContractRegistry,
    ResourceResolverRegistry,
)
from src.domains.agentverse.activities.tests.test_activity_generic import (  # noqa: F401
    Attempt,
    actor,
    env,
    start,
    switch,
)
from src.domains.agentverse.agents.conversational_composition_v2 import (
    ALL_MODES,
    LEGACY_CONVERSATION_COMPATIBILITY,
    ConversationGate,
    LegacyConversationEntry,
    resolve_conversation,
)
from src.domains.agentverse.agents.creation_drafts.activity import AGENT_CREATION_INTERVIEW

INTERVIEW = "agent.creation_interview@1.0.0"
LATER = "test.later_activity@1.0.0"
ENKI = "00000000-0000-4000-8000-000000000004"
THOTH = "00000000-0000-4000-8000-000000000006"
NABU = "697e8c4b-171a-b672-c90e-f4b6ca5dd05b"
LEGACY = {
    ENKI: ("form", "enki"),
    THOTH: ("chat", "thoth"),
    NABU: ("chat", "general"),
}


class _Resolver:
    def __init__(self, kind: str) -> None:
        self.kind = kind

    async def resolve(self, actor, resource_id):  # pragma: no cover - never resolved here
        raise AssertionError("not used")


def contracts(*extra) -> ActivityContractRegistry:
    resolvers = ResourceResolverRegistry()
    for kind in ("manuscript.session", "agent.creation_draft", "agent.draft.proposal"):
        resolvers.register(_Resolver(kind))
    resolvers.freeze()
    registry = ActivityContractRegistry(resolvers, frozenset({"conversation.v2"}))
    registry.register(AGENT_CREATION_INTERVIEW)
    for contract in extra:
        registry.register(contract)
    registry.freeze()
    return registry


# A conversation-owned contract registered after the compatibility entries were reviewed.
LATER_CONTRACT = dataclasses.replace(
    AGENT_CREATION_INTERVIEW, id="test.later_activity", title="Later activity"
)


def legacy(agent_id: str, **changes) -> dict:
    agent_type, role = LEGACY[agent_id]
    return {
        "id": agent_id,
        "tenant_id": "caos-local",
        "runtime_version": "v2",
        "type": agent_type,
        "role_name": role,
        "tools": [],
        **changes,
    }


def gate_for(definition: dict, registry=None) -> ConversationGate:
    gate = ConversationGate(
        definitions=SimpleNamespace(find_by_id=AsyncMock(return_value=definition))
    )
    if registry is not None:
        gate.bind_activities(registry)
    return gate


def owner_for(gate: ConversationGate | None, *, authority=None) -> ConversationOwner:
    return ConversationOwner(
        conversations=SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace())),
        authority=authority,
        activities=None if gate is None else gate.declared_activities,
    )


def principal(agent_id: str, grants=frozenset({TURN_SIGNAL})):
    return SimpleNamespace(
        tenant_id="caos-local", principal_id=agent_id, session_id="s", grants=grants
    )


# --- Legacy compatibility declarations -------------------------------------------------------


def test_legacy_entries_declare_exact_activities_and_keep_their_modes():
    entries = {e.agent_id: e for e in LEGACY_CONVERSATION_COMPATIBILITY}
    assert set(entries) == set(LEGACY)
    assert entries[ENKI].activities == (INTERVIEW,)
    assert entries[THOTH].activities == () and entries[NABU].activities == ()
    for agent_id in LEGACY:
        composition = resolve_conversation(legacy(agent_id))
        assert composition.source == "legacy" and composition.available
        assert tuple(composition.config.modes) == ALL_MODES  # unchanged by MIG-FIX-0.1
        assert composition.config.activities == list(entries[agent_id].activities)


def test_legacy_declarations_are_validated_like_an_attachment():
    with pytest.raises(ValueError):
        LegacyConversationEntry("x", "t", "a", "chat", "general", activities=("not a key",))
    with pytest.raises(ValueError):
        LegacyConversationEntry("x", "t", "a", "chat", "general", activities=(INTERVIEW,) * 2)


@pytest.mark.asyncio
async def test_unbound_or_missing_contracts_never_change_legacy_modes():
    # Without a bound registry (or without the contract), Enki still converses exactly as
    # before; it simply declares nothing it could start.
    empty = ResourceResolverRegistry()
    empty.freeze()
    without = ActivityContractRegistry(empty, frozenset({"conversation.v2"}))
    without.freeze()
    for registry in (None, without):
        gate = gate_for(legacy(ENKI), registry)
        for mode in ALL_MODES:
            assert (await gate.require("caos-local", ENKI, mode)).modes == list(ALL_MODES)
        assert await gate.declared_activities("caos-local", ENKI) == frozenset()


# --- Consent through the conversation.v2 owner ------------------------------------------------


@pytest.mark.asyncio
async def test_enki_consents_to_its_declared_creation_interview():
    gate = gate_for(legacy(ENKI), contracts())
    assert await gate.declared_activities("caos-local", ENKI) == frozenset({INTERVIEW})
    assert await owner_for(gate).permits_activity(principal(ENKI), "c", INTERVIEW)


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_id", [THOTH, NABU])
async def test_thoth_and_nabu_are_denied_creation_interviews(agent_id):
    gate = gate_for(legacy(agent_id), contracts())
    assert await gate.declared_activities("caos-local", agent_id) == frozenset()
    assert not await owner_for(gate).permits_activity(principal(agent_id), "c", INTERVIEW)


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_id", [ENKI, THOTH, NABU])
async def test_a_newly_registered_contract_is_never_implied_for_legacy_entries(agent_id):
    gate = gate_for(legacy(agent_id), contracts(LATER_CONTRACT))
    declared = await gate.declared_activities("caos-local", agent_id)
    assert LATER not in declared
    assert not await owner_for(gate).permits_activity(principal(agent_id), "c", LATER)


@pytest.mark.asyncio
async def test_an_explicit_conversation_tool_without_activities_declares_none():
    definition = legacy(
        NABU, tools=[{"key": "conversation", "config": {"modes": ["context", "llm"]}}]
    )
    gate = gate_for(definition, contracts(LATER_CONTRACT))
    assert gate.resolve(definition).source == "tool"
    assert await gate.declared_activities("caos-local", NABU) == frozenset()
    owner = owner_for(gate)
    for activity in (INTERVIEW, LATER):
        assert not await owner.permits_activity(principal(NABU), "c", activity)
    # Declared explicitly, the same contract is consented to.
    definition["tools"][0]["config"]["activities"] = [LATER]
    assert await owner.permits_activity(principal(NABU), "c", LATER)
    assert not await owner.permits_activity(principal(NABU), "c", INTERVIEW)


@pytest.mark.asyncio
async def test_a_missing_declaration_source_denies():
    assert not await owner_for(None).permits_activity(principal(ENKI), "c", INTERVIEW)
    # A source that answers anything but an explicit set is not a declaration.
    for answer in (None, {INTERVIEW}, [INTERVIEW]):
        owner = ConversationOwner(
            conversations=None, authority=None, activities=AsyncMock(return_value=answer)
        )
        assert not await owner.permits_activity(principal(ENKI), "c", INTERVIEW)


@pytest.mark.asyncio
async def test_consent_never_replaces_utu_authorization():
    gate = gate_for(legacy(ENKI), contracts())
    denied = SimpleNamespace(
        authorize_conversation=AsyncMock(side_effect=EventPermissionError("no access"))
    )
    owner = owner_for(gate, authority=denied)
    # Declared, but without the conversational turn grant: not found.
    without_grant = principal(ENKI, grants=frozenset())
    assert await owner.permits_activity(without_grant, "c", INTERVIEW)
    with pytest.raises(ActivityNotFound):
        await owner.authorize(without_grant, "c")
    # Declared and granted, but UTU denies the conversation: not found.
    with pytest.raises(ActivityNotFound):
        await owner.authorize(principal(ENKI), "c")
    denied.authorize_conversation.assert_awaited_once()


# --- Generic service (real Mongo) ------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_owner_kind_without_consent_never_starts_or_switches(env):  # noqa: F811
    e = env
    context, _ = await start(e, "op-start-00000000001")
    threads = e.threads

    class NoConsent:  # the same owner kind, with no consent method at all
        kind, audit_signal, audit_version = (
            threads.kind,
            threads.audit_signal,
            threads.audit_version,
        )
        authorize = threads.authorize
        causation = threads.causation

    e.service._owners[threads.kind] = NoConsent()
    with pytest.raises(ActivityContractUnavailable):
        await switch(e, context, "op-switch-0000000001")
    e.service._owners[threads.kind] = threads
    threads.permits_activity = None  # a consent attribute that is not callable consent
    with pytest.raises(ActivityContractUnavailable):
        await switch(e, context, "op-switch-0000000002")
    # The existing context is unaffected: readable, replayable, closable, reconcilable.
    assert (await e.service.active(e.actor, e.owner)).context_id == context.context_id
    replayed, created = await start(e, "op-start-00000000001")
    assert not created and replayed.context_id == context.context_id
    closed = await e.service.close(
        e.actor,
        Attempt("thread-1", "close", 99),
        context_id=context.context_id,
        expected_revision=context.revision,
        reason="completed",
        operation_id="op-close-0000000001",
    )
    assert closed.status == "closed"
    assert await e.service.reconcile(e.actor, e.owner) == 0
    with pytest.raises(ActivityContractUnavailable):
        await start(e, "op-start-00000000002", doc="doc-2")


@pytest.mark.asyncio
async def test_consent_is_checked_after_owner_authorization(env):  # noqa: F811
    e = env
    outsider = actor(subject="stranger")
    with pytest.raises(ActivityNotFound):  # declared, but not a member of the owner
        await start(e, "op-start-00000000003", who=outsider)
