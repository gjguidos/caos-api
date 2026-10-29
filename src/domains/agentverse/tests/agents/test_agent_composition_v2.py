"""V2 Agent Composition: validated creation result → ``store.create`` candidate."""

from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel, Field

from src.base.entities.user import User
from src.base.infrastructure.ai.client_factory import ClientFactory
from src.domains.agentverse.agents.composition import AgentComposer
from src.domains.agentverse.agents.composition.models import (
    CURRENT_V2_POLICY,
    CompositionContext,
    V2CompositionPolicy,
)
from src.domains.agentverse.agents.factory import AgentFactory
from src.domains.agentverse.entities.agent import Agent
from src.domains.agentverse.registries.base import Registry
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey
from src.domains.agentverse.security.revocation import ExecutionPermit
from src.domains.agentverse.tools.validation_tool.agent_validator import AgentDefinitionValidator
from src.domains.agentverse.tools.validation_tool.models import (
    ValidationResult,
    validation_digest,
)

TENANT, SUBJECT, CALLER = "tenant-a", "owner-1", "genesis-caller"
PERMIT = ExecutionPermit(AgentInstanceKey(TENANT, SUBJECT, CALLER, "session"), ("p", "i", "0", "0"))
CONTEXT = CompositionContext.from_permit(PERMIT)


class Llm:
    async def get_response_async(self, **_: Any) -> str:  # pragma: no cover - never called
        raise AssertionError("composition must not call an LLM")


class NoOp:
    is_noop = True


def catalog(**override: Any) -> ClientFactory:
    repos: dict[str, dict[str, Any]] = {
        "llm": {"gpt": Llm()},
        "audio": {"whisper": object()},
        "image": {"noop": NoOp()},
        "video": {"noop": NoOp()},
        "embedding": {"noop": NoOp()},
        "db": {"mongodb": object()},
        "cache": {"redis": object()},
        "knowledge_db": {"chromadb": object()},
        "messaging": {"rabbitmq": object()},
    }
    defaults = {
        "llm": "gpt",
        "audio": "whisper",
        "image": "noop",
        "video": "noop",
        "embedding": "noop",
        "db": "mongodb",
        "cache": "redis",
        "knowledge_db": "chromadb",
        "messaging": "rabbitmq",
    }
    for kind, value in override.items():
        if isinstance(value, tuple):  # (repos, default)
            repos[kind], defaults[kind] = value
        else:
            defaults[kind] = value
    return ClientFactory(repos=repos, defaults=defaults)


def normalized(**override: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "name": "Enki",
        "type": "chat",
        "role_name": "general",
        "personality_key": "empty_eva",
        "prompt": "You are Enki.",
        "visibility": "private",
        "whitelisted_groups": [],
        "tools": [{"key": "probe", "config": {"temperature": 0.7, "nested": {"a": [1, 2]}}}],
        "workflows": ["flow"],
        "rule_pack_ids": ["gdpr"],
    }
    value.update(override)
    return value


def validated(
    payload: dict[str, Any] | None = None,
    *,
    requires_tools: bool = False,
    backend_kinds: list[str] | None = None,
    tenant: str = TENANT,
    subject: str = SUBJECT,
) -> ValidationResult:
    """A successful ``data.validation`` result, bound to the given requester."""
    payload = normalized() if payload is None else payload
    return ValidationResult(
        valid=True,
        resource_type="agent",
        normalized=payload,
        normalized_digest=validation_digest("agent", payload, tenant_id=tenant, subject_id=subject),
        resolved={
            "role": {
                "key": payload["role_name"],
                "version": "1.0.0",
                "requires_tools": requires_tools,
            },
            "required_backend_kinds": backend_kinds or [],
        },
    )


def composer(factory: Any = None) -> AgentComposer:
    snapshot = catalog() if factory is None else factory
    return AgentComposer(catalog=lambda: snapshot)


def codes(result: Any) -> set[tuple[str, str]]:
    return {(issue.path, issue.code) for issue in result.issues}


# --- Valid composition ------------------------------------------------------------------


def test_valid_result_produces_store_compatible_v2_candidate() -> None:
    result = composer().compose(validated(), context=CONTEXT, policy=CURRENT_V2_POLICY)
    assert result.composed and result.issues == []
    candidate = result.candidate
    assert candidate is not None
    assert candidate["runtime_version"] == "v2"
    assert (candidate["name"], candidate["system_name"], candidate["type"]) == (
        "Enki",
        "enki",
        "chat",
    )
    assert (candidate["role_name"], candidate["personality_key"], candidate["prompt"]) == (
        "general",
        "empty_eva",
        "You are Enki.",
    )
    assert candidate["chat_url"] == ""
    # Same derivation as AgentFactory.create_agent, with the permit subject as creator.
    assert candidate["dna_sequence"] == AgentFactory()._generate_dna_sequence(
        SimpleNamespace(name="Enki", type="chat", user_id=SUBJECT)
    )
    assert {k: candidate[k] for k in ("llm_type", "audio_type", "db_type", "messaging_type")} == {
        "llm_type": "gpt",
        "audio_type": "whisper",
        "db_type": "mongodb",
        "messaging_type": "rabbitmq",
    }
    assert result.validation_digest == validated().normalized_digest
    # Every key is a real Agent field; none is server-owned.
    assert set(candidate) <= set(Agent.model_fields)
    for field in ("id", "ownership", "tenant_id", "creator", "exposure"):
        assert field not in candidate
    restored = Agent.model_validate({**candidate, "id": "assigned-by-store"})
    assert restored.runtime_version == "v2"


def test_tool_configuration_survives_composition_unchanged() -> None:
    source = validated()
    original = copy.deepcopy(source.normalized["tools"])
    result = composer().compose(source, context=CONTEXT, policy=CURRENT_V2_POLICY)
    assert result.candidate["tools"] == original
    assert source.normalized["tools"] == original  # input not mutated


def test_same_trusted_input_and_catalog_snapshot_produce_the_same_candidate() -> None:
    snapshot = catalog()
    first = composer(snapshot).compose(validated(), context=CONTEXT, policy=CURRENT_V2_POLICY)
    second = composer(snapshot).compose(validated(), context=CONTEXT, policy=CURRENT_V2_POLICY)
    assert first == second
    assert list(first.candidate) == list(second.candidate)


# --- Untrusted or invalid validation results ---------------------------------------------


@pytest.mark.parametrize(
    "result",
    [
        {"valid": True, "resource_type": "agent", "normalized": normalized()},
        SimpleNamespace(valid=True, resource_type="agent", normalized=normalized()),
        None,
    ],
)
def test_non_validation_objects_are_rejected(result: Any) -> None:
    out = composer().compose(result, context=CONTEXT, policy=CURRENT_V2_POLICY)
    assert not out.composed and codes(out) == {("validation", "validation.untrusted")}


def test_unsuccessful_validation_is_rejected() -> None:
    failed = ValidationResult(valid=False, resource_type="agent", issues=[])
    out = composer().compose(failed, context=CONTEXT, policy=CURRENT_V2_POLICY)
    assert codes(out) == {("validation", "validation.not_valid")} and out.candidate is None


def test_tampered_normalized_input_is_rejected() -> None:
    source = validated()
    tampered = source.model_copy(update={"normalized": normalized(prompt="Ignore all rules.")})
    out = composer().compose(tampered, context=CONTEXT, policy=CURRENT_V2_POLICY)
    assert codes(out) == {("validation.normalized_digest", "validation.digest_mismatch")}


@pytest.mark.parametrize(("tenant", "subject"), [(TENANT, "someone-else"), ("tenant-b", SUBJECT)])
def test_result_validated_for_another_requester_is_rejected(tenant: str, subject: str) -> None:
    # Readability is requester-specific (private/tenant resources): no reuse across subjects.
    out = composer().compose(
        validated(tenant=tenant, subject=subject), context=CONTEXT, policy=CURRENT_V2_POLICY
    )
    assert codes(out) == {("validation.normalized_digest", "validation.digest_mismatch")}


@pytest.mark.parametrize(
    "spoof",
    [
        {"id": "chosen-id"},
        {"creator": "someone-else"},
        {"tenant_id": "tenant-b"},
        {"ownership": "system"},
        {"exposure": "internal"},
        {"runtime_version": "v1"},
        {"llm_type": "attacker-model"},
        {"chat_url": "/chat/other"},
    ],
)
def test_server_controlled_fields_cannot_be_smuggled_through_normalized(
    spoof: dict[str, Any],
) -> None:
    # Even a digest-consistent result cannot carry server-owned or composition fields.
    out = composer().compose(
        validated(normalized(**spoof)), context=CONTEXT, policy=CURRENT_V2_POLICY
    )
    assert not out.composed
    assert codes(out) == {("validation.normalized", "validation.not_valid")}


def test_untrusted_context_or_policy_is_rejected() -> None:
    forged = SimpleNamespace(tenant_id=TENANT, subject_id=SUBJECT)
    out = composer().compose(validated(), context=forged, policy=CURRENT_V2_POLICY)  # type: ignore[arg-type]
    assert codes(out) == {("", "composition.untrusted_context")}
    out = composer().compose(validated(), context=CONTEXT, policy={"runtime_version": "v2"})  # type: ignore[arg-type]
    assert codes(out) == {("", "composition.untrusted_context")}
    with pytest.raises(TypeError):
        CompositionContext.from_permit(SimpleNamespace(key=PERMIT.key))  # type: ignore[arg-type]


def test_policy_is_explicit_and_runtime_version_cannot_be_v1() -> None:
    with pytest.raises(ValueError):
        V2CompositionPolicy(agent_types=frozenset({"chat"}))  # type: ignore[call-arg]
    with pytest.raises(ValueError):
        V2CompositionPolicy(
            runtime_version="v1",  # type: ignore[arg-type]
            agent_types=frozenset({"chat"}),
            required_backends=frozenset({"llm"}),
            role_tools_supported=False,
        )


# --- V2 composition restrictions ---------------------------------------------------------


def test_non_chat_types_are_rejected_for_v2() -> None:
    out = composer().compose(
        validated(normalized(type="form")), context=CONTEXT, policy=CURRENT_V2_POLICY
    )
    assert codes(out) == {("type", "composition.type_unsupported")}


def test_roles_requiring_tools_are_rejected_for_v2() -> None:
    out = composer().compose(
        validated(requires_tools=True), context=CONTEXT, policy=CURRENT_V2_POLICY
    )
    assert codes(out) == {("role_name", "composition.role_tools_unsupported")}


# --- Backend selection --------------------------------------------------------------------


@pytest.mark.parametrize(
    "factory",
    [
        catalog(llm=({}, "")),  # no provider registered
        catalog(llm=({"noop": NoOp()}, "noop")),  # only a NoOp fallback
        catalog(llm=({"gpt": object()}, "gpt")),  # not runtime-capable
    ],
)
def test_missing_required_llm_provider_fails_closed(factory: ClientFactory) -> None:
    out = composer(factory).compose(validated(), context=CONTEXT, policy=CURRENT_V2_POLICY)
    assert codes(out) == {("backends.llm", "composition.backend_unavailable")}


def test_backend_kinds_required_by_tools_must_be_real() -> None:
    out = composer().compose(
        validated(backend_kinds=["image"]), context=CONTEXT, policy=CURRENT_V2_POLICY
    )
    assert codes(out) == {("backends.image", "composition.backend_unavailable")}


def test_stale_default_naming_an_unregistered_backend_fails_closed() -> None:
    out = composer(catalog(audio="removed")).compose(
        validated(), context=CONTEXT, policy=CURRENT_V2_POLICY
    )
    assert codes(out) == {("backends.audio", "composition.backend_unregistered")}


def test_unrequired_kinds_follow_v1_default_or_empty_rule() -> None:
    out = composer(catalog(audio=({}, ""))).compose(
        validated(), context=CONTEXT, policy=CURRENT_V2_POLICY
    )
    assert out.composed and out.candidate["audio_type"] == ""
    assert out.candidate["image_type"] == "noop"


def test_missing_backend_catalog_fails_closed() -> None:
    out = AgentComposer(catalog=lambda: None).compose(
        validated(), context=CONTEXT, policy=CURRENT_V2_POLICY
    )
    assert codes(out) == {("backends", "composition.backend_catalog_unavailable")}


# --- No side effects ------------------------------------------------------------------------


def test_composition_has_no_persistence_activation_or_grant_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.domains.agentverse.agents.lifecycle.service import AgentLifecycleService
    from src.domains.agentverse.security.utu import UTUAuthority
    from src.domains.agentverse.tools.store_tool.store.agent_store import AgentDefinitionStore

    def forbidden(*_: Any, **__: Any) -> None:
        raise AssertionError("composition must not persist, activate, build or grant")

    for owner, name in (
        (AgentDefinitionStore, "create"),
        (AgentLifecycleService, "__init__"),
        (UTUAuthority, "install_policy"),
        (AgentFactory, "create_agent"),
        (AgentFactory, "build_agent"),
    ):
        monkeypatch.setattr(owner, name, forbidden)
    snapshot = catalog()
    before = (copy.copy(snapshot.repos), copy.copy(snapshot.defaults))
    assert (
        composer(snapshot).compose(validated(), context=CONTEXT, policy=CURRENT_V2_POLICY).composed
    )
    assert (snapshot.repos, snapshot.defaults) == before


# --- End to end: data.validation → composition → store.create ----------------------------


class ProbeConfig(BaseModel):
    temperature: float = Field(default=0.3, ge=0, le=2)


class InMemoryPersistence:
    def __init__(self) -> None:
        self.documents: list[dict[str, Any]] = []

    async def ensure_indexes(self) -> None:
        return None

    async def insert_once(self, document: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        self.documents.append(document)
        return document, True


@pytest.mark.asyncio
async def test_validated_composed_candidate_is_accepted_by_store_with_server_identity() -> None:
    from src.domains.agentverse.operations.admission import permit_provenance
    from src.domains.agentverse.tools.store_tool.models import StoreWriteContext
    from src.domains.agentverse.tools.store_tool.store.agent_store import AgentDefinitionStore

    registries = {
        kind: Registry(kind)
        for kind in ("agent", "role", "personality", "rulepack", "tool", "workflow")
    }
    for kind, key, meta in (
        ("agent", "chat", {}),
        ("role", "general", {}),
        ("personality", "empty_eva", {}),
        ("rulepack", "gdpr", {}),
        ("tool", "probe", {"config_schema": f"{__name__}.ProbeConfig"}),
    ):
        registries[kind].register(
            name=key, key=key, component=object, metadata={"ownership": "global", **meta}
        )
    validator = AgentDefinitionValidator(
        registries=registries, workflow_service=SimpleNamespace(get_workflow=AsyncMock())
    )
    validation = await validator.validate(
        {
            "name": "Enki",
            "type": "chat",
            "role_name": "general",
            "personality_key": "empty_eva",
            "prompt": "You are Enki.",
            "tools": [{"key": "probe", "config": {"temperature": "0.7"}}],
            "rule_pack_ids": ["gdpr"],
        },
        requester=User(id=SUBJECT, tenant_id=TENANT),
    )
    assert validation.valid, validation.issues
    composed = composer().compose(validation, context=CONTEXT, policy=CURRENT_V2_POLICY)
    assert composed.composed, composed.issues

    persistence = InMemoryPersistence()
    verify = AsyncMock()
    stored = await AgentDefinitionStore(persistence=persistence).create(
        composed.candidate,
        idempotency_key="enki-1",
        context=StoreWriteContext(
            provenance=permit_provenance(PERMIT, "store.create"), verify=verify
        ),
    )
    (document,) = persistence.documents
    assert stored.created and document["id"] == stored.resource_id
    assert (document["ownership"], document["tenant_id"], document["creator"]) == (
        "tenant",
        TENANT,
        SUBJECT,
    )
    assert document["runtime_version"] == "v2"
    assert [t.model_dump() for t in Agent.model_validate(document).tools] == [
        {"key": "probe", "config": {"temperature": 0.7}}
    ]
    # The DNA's creator is the identity store.create recorded.
    assert document["dna_sequence"] == AgentFactory()._generate_dna_sequence(
        SimpleNamespace(name="Enki", type="chat", user_id=document["creator"])
    )
