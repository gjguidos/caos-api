"""``store.create.provisioned``: policy-pinned Enki creation through real CPBX/UTU."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
from dependency_injector import providers

from src.base.entities.user import User
from src.base.events.v2.contracts import EventPermissionError
from src.base.infrastructure.ai.client_factory import ClientFactory
from src.domains.agentverse.agents.composition import AgentComposer
from src.domains.agentverse.agents.composition.models import (
    CURRENT_V2_POLICY,
    CompositionContext,
)
from src.domains.agentverse.agents.lifecycle.agent_principal_resolver import (
    AgentActivationApproval,
    AgentPrincipalResolver,
    VerifiedAgentSession,
)
from src.domains.agentverse.entities.agent import Agent
from src.domains.agentverse.ncsc.authorization import SynapticAuthorization
from src.domains.agentverse.ncsc.cpbx import SynapticExchange
from src.domains.agentverse.ncsc.models import Synapse, SynapticGrants, TrustedLine
from src.domains.agentverse.registries.base import Registry
from src.domains.agentverse.registries.synaptic_registry import SynapticRegistry
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey
from src.domains.agentverse.security import agent_access
from src.domains.agentverse.security.revocation import ExecutionPermit, policy_version
from src.domains.agentverse.security.utu import AuthorizedAgentPrincipal, UTUAuthority
from src.domains.agentverse.tools.store_tool import (
    ProvisionedStoreCapability,
    register_store_create,
    register_store_create_provisioned,
)
from src.domains.agentverse.tools.store_tool.dependencies.di_container import (
    StoreToolContainer,
)
from src.domains.agentverse.tools.store_tool.models import ProvisionedIdentity
from src.domains.agentverse.tools.store_tool.spec import (
    STORE_CREATE_PROVISIONED_SIGNAL,
    STORE_CREATE_PROVISIONED_VERSION,
    STORE_CREATE_SIGNAL,
    STORE_PROVIDER,
)
from src.domains.agentverse.tools.validation_tool.agent_validator import AgentDefinitionValidator

# Same identifiers as the V2 response fixtures, so the stored Enki can be served by them.
TENANT, OWNER, ENKI = "tenant", "owner", "agent"
GENESIS = "genesis-provisioner"
KEY = AgentInstanceKey(TENANT, OWNER, GENESIS, "genesis-session")

ENKI_SLOT = ProvisionedIdentity(
    slot="enki",
    resource_type="agent",
    agent_id=ENKI,
    tenant_id=TENANT,
    principal_id=GENESIS,
    subject_id=OWNER,
    name="Enki",
    type="chat",
    runtime_version="v2",
)
GENESIS_GRANTS = {"data.validation", STORE_CREATE_PROVISIONED_SIGNAL}


def candidate(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "runtime_version": "v2",
        "name": "Enki",
        "system_name": "enki",
        "type": "chat",
        "role_name": "general",
        "personality_key": "empty_eva",
        "prompt": "You are Enki, keeper of CAOS agents.",
        "chat_url": "",
        "dna_sequence": "enki-dna",
        "llm_type": "registered-model",
        "audio_type": "",
        "db_type": None,
        "cache_type": None,
        "knowledge_db_type": None,
        "messaging_type": None,
        "visibility": "private",
    }
    value.update(overrides)
    return value


def body(payload: dict[str, Any] | None = None, *, slot: str = "enki", **extra: Any) -> dict:
    return {
        "resource_type": "agent",
        "slot": slot,
        "payload": candidate() if payload is None else payload,
        **extra,
    }


class FakePersistence:
    """Enforces the unique store scope like the Mongo index; records every access."""

    def __init__(self) -> None:
        self.documents: list[dict[str, Any]] = []
        self.calls: list[str] = []

    async def ensure_indexes(self) -> None:
        self.calls.append("ensure_indexes")

    async def find_by_id(self, agent_id: str) -> dict[str, Any] | None:
        self.calls.append("find_by_id")
        return next((dict(d) for d in self.documents if d.get("id") == agent_id), None)

    async def insert_once(self, document: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        self.calls.append("insert_once")
        scope = document["store"]["idempotency_scope"]
        for row in self.documents:
            if row["store"]["idempotency_scope"] == scope:
                return dict(row), False
        self.documents.append(dict(document))
        return document, True


def authority(
    *, grants: dict[tuple[str, str], set[str]], trusted_lines: tuple = (), stale: bool = False
) -> tuple[UTUAuthority, tuple[str, ...], Any]:
    policy = SynapticAuthorization(
        grants={scope: SynapticGrants(frozenset(g)) for scope, g in grants.items()},
        trusted_lines=trusted_lines,
    )
    current = (policy_version(policy), "incarnation", "0", "0")
    resolver = AgentPrincipalResolver(
        definitions=SimpleNamespace(find_by_id=AsyncMock()),
        sessions=SimpleNamespace(get_session=AsyncMock(), bind_revocation_handler=Mock()),
        authorization=policy,
        approvals={},
    )
    revocations = SimpleNamespace(
        read=AsyncMock(return_value=("old", "incarnation", "0", "0") if stale else current)
    )
    utu = UTUAuthority(resolver=resolver, authorization=policy, revocations=revocations)
    return utu, current, revocations


def stack(
    *,
    grants: dict[tuple[str, str], set[str]] | None = None,
    provisions: dict[str, ProvisionedIdentity] | None = None,
    key: AgentInstanceKey = KEY,
    **kwargs: Any,
) -> SimpleNamespace:
    """Production composition: StoreToolContainer + SynapticRegistry + CPBX + UTU."""
    grants = {(TENANT, GENESIS): GENESIS_GRANTS} if grants is None else grants
    utu, versions, revocations = authority(grants=grants, **kwargs)
    persistence = FakePersistence()
    container = StoreToolContainer(
        mongodb_repository=providers.Object(Mock()), utu_authority=providers.Object(utu)
    )
    container.agent_definition_repository.override(providers.Object(persistence))
    registry = SynapticRegistry()
    register_store_create(registry, container.tool())
    capability = ProvisionedStoreCapability(
        tool=container.tool(),
        provisions={"enki": ENKI_SLOT} if provisions is None else provisions,
    )
    register_store_create_provisioned(registry, capability)
    exchange = SynapticExchange(registry=registry, authorization=utu)
    client = exchange.bind(
        AuthorizedAgentPrincipal(key.agent_id, key.tenant_id, ExecutionPermit(key, versions))
    )
    return SimpleNamespace(
        client=client,
        exchange=exchange,
        persistence=persistence,
        capability=capability,
        registry=registry,
        revocations=revocations,
        versions=versions,
    )


async def provision(s: SimpleNamespace, request: dict[str, Any]) -> dict[str, Any]:
    return await s.client.request(STORE_CREATE_PROVISIONED_SIGNAL, request)


# --- Restricted creation ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_genesis_creates_exactly_the_pinned_tenant_owned_enki() -> None:
    s = stack()
    result = await provision(s, body())
    assert result["success"] is True, result
    assert result["result"]["resource_id"] == ENKI and result["result"]["created"] is True
    (document,) = s.persistence.documents
    assert document["id"] == ENKI
    assert (document["ownership"], document["tenant_id"], document["creator"]) == (
        "tenant",
        TENANT,
        OWNER,
    )
    assert (document["exposure"], document["runtime_version"]) == ("public", "v2")
    record = document["store"]["authorization"]
    assert (record["tenant_id"], record["principal_id"], record["subject_id"]) == (
        TENANT,
        GENESIS,
        OWNER,
    )
    assert "genesis-session" not in repr(document)
    Agent.model_validate(document)


@pytest.mark.asyncio
async def test_replay_is_idempotent_and_a_different_definition_conflicts() -> None:
    s = stack()
    first = await provision(s, body())
    again = await provision(s, body())
    assert again["success"] and again["result"]["created"] is False
    assert again["result"]["resource_id"] == first["result"]["resource_id"] == ENKI
    changed = await provision(s, body(candidate(prompt="Something else.")))
    assert changed["meta"]["code"] == "STORE_IDEMPOTENCY_CONFLICT"
    assert len(s.persistence.documents) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("slot", ["adam", "ENKI", "enki2", "x" * 64])
async def test_unrelated_slots_are_denied_before_any_read_or_write(slot: str) -> None:
    s = stack()
    with pytest.raises(EventPermissionError):
        await provision(s, body(slot=slot))
    assert s.persistence.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("slot", ["", "x" * 65, None])
async def test_malformed_slots_are_invalid_requests(slot: Any) -> None:
    s = stack()
    assert (await provision(s, body(slot=slot)))["meta"]["code"] == "STORE_INVALID_PAYLOAD"
    assert s.persistence.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "key",
    [
        AgentInstanceKey(TENANT, OWNER, "other-principal", "s"),  # another granted principal
        AgentInstanceKey(TENANT, "other-user", GENESIS, "s"),  # Genesis for another subject
        AgentInstanceKey("tenant-b", OWNER, GENESIS, "s"),  # Genesis in another tenant
    ],
)
async def test_unauthorized_principals_subjects_and_tenants_are_denied(
    key: AgentInstanceKey,
) -> None:
    s = stack(grants={(key.tenant_id, key.agent_id): GENESIS_GRANTS}, key=key)
    with pytest.raises(EventPermissionError):
        await provision(s, body())
    assert s.persistence.calls == []


@pytest.mark.asyncio
async def test_superseded_or_missing_permits_are_denied() -> None:
    from src.base.events.v2.policy import EventPrincipal

    stale = stack(stale=True)
    with pytest.raises(EventPermissionError):
        await provision(stale, body())
    s = stack()
    unpermitted = s.exchange.bind(EventPrincipal(GENESIS, TENANT))
    with pytest.raises(EventPermissionError):
        await unpermitted.request(STORE_CREATE_PROVISIONED_SIGNAL, body())
    assert stale.persistence.documents == s.persistence.documents == []


@pytest.mark.asyncio
async def test_direct_handler_call_with_forged_or_mismatched_permit_is_denied() -> None:
    s = stack()
    forged = SimpleNamespace(execution_permit=ExecutionPermit(KEY, s.versions))
    for source, tenant in (("someone-else", TENANT), (GENESIS, "tenant-b")):
        synapse = Synapse(STORE_CREATE_PROVISIONED_SIGNAL, source, STORE_PROVIDER, tenant, body())
        with pytest.raises(EventPermissionError):
            await s.capability.create(synapse, forged)  # type: ignore[arg-type]
    synapse = Synapse(STORE_CREATE_PROVISIONED_SIGNAL, GENESIS, STORE_PROVIDER, TENANT, body())
    with pytest.raises(EventPermissionError):
        await s.capability.create(synapse, SimpleNamespace(execution_permit=None))  # type: ignore[arg-type]
    assert s.persistence.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "override", [{"name": "Not Enki"}, {"type": "orchestration"}, {"runtime_version": "v1"}]
)
async def test_payloads_not_matching_the_pinned_identity_are_rejected(
    override: dict[str, Any],
) -> None:
    s = stack()
    result = await provision(s, body(candidate(**override)))
    assert result["meta"]["code"] == "STORE_INVALID_PAYLOAD"
    assert s.persistence.documents == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "override",
    [
        {"id": "chosen-id"},
        {"ownership": "system"},
        {"ownership": "global", "tenant_id": None},
        {"tenant_id": "tenant-b"},
        {"creator": "someone-else"},
        {"exposure": "internal"},
    ],
)
async def test_server_owned_identity_and_system_ownership_cannot_be_supplied(
    override: dict[str, Any],
) -> None:
    s = stack()
    result = await provision(s, body(candidate(**override)))
    assert result["meta"]["code"] == "STORE_OWNERSHIP_VIOLATION"
    assert s.persistence.documents == []


@pytest.mark.asyncio
async def test_payload_may_repeat_exact_derived_values() -> None:
    exact = {"id": ENKI, "ownership": "tenant", "tenant_id": TENANT, "creator": OWNER}
    s = stack()
    assert (await provision(s, body(candidate(**exact, exposure="public"))))["success"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra",
    [{"agent_id": "x"}, {"ownership": "system"}, {"idempotency_key": "k"}, {"tenant_id": TENANT}],
)
async def test_request_cannot_name_identity_ownership_or_idempotency(extra: dict) -> None:
    s = stack()
    result = await provision(s, body(**extra))
    assert result["meta"]["code"] == "STORE_INVALID_PAYLOAD"
    assert s.persistence.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        candidate(genesis_local_seed=True),
        candidate(store={"forged": True}),
        candidate(commbridge="x"),
        candidate(unknown_field=1),
    ],
)
async def test_arbitrary_definition_fields_are_rejected(payload: dict[str, Any]) -> None:
    s = stack()
    assert (await provision(s, body(payload)))["meta"]["code"] == "STORE_INVALID_PAYLOAD"


@pytest.mark.asyncio
async def test_pinned_id_already_held_outside_the_scope_conflicts() -> None:
    s = stack()
    s.persistence.documents.append({"id": ENKI, "store": {"idempotency_scope": "seeded"}})
    s.persistence.documents.append({"id": ENKI + "-v1", "store": {"idempotency_scope": "x"}})
    result = await provision(s, body())
    assert result["meta"]["code"] == "STORE_CONFLICT"
    assert "insert_once" not in s.persistence.calls


@pytest.mark.asyncio
async def test_authority_revoked_before_the_write_boundary_prevents_the_write() -> None:
    s = stack()
    calls = 0

    async def read(_: Any) -> tuple[str, ...]:
        nonlocal calls
        calls += 1
        return s.versions if calls <= 2 else ("revoked", "incarnation", "0", "0")

    s.revocations.read = read
    with pytest.raises(EventPermissionError):
        await provision(s, body())
    assert s.persistence.documents == []


# --- Least privilege and tenant-path regression ------------------------------------------


@pytest.mark.asyncio
async def test_provisioned_grant_does_not_confer_general_store_create() -> None:
    s = stack()
    with pytest.raises(EventPermissionError):
        await s.client.request(
            STORE_CREATE_SIGNAL,
            {"resource_type": "agent", "payload": candidate(name="Rogue"), "idempotency_key": "k"},
        )
    assert s.persistence.calls == []


@pytest.mark.asyncio
async def test_ordinary_store_create_keeps_scope_derived_tenant_identity() -> None:
    s = stack(grants={(TENANT, GENESIS): {STORE_CREATE_SIGNAL}})
    result = await s.client.request(
        STORE_CREATE_SIGNAL,
        {"resource_type": "agent", "payload": candidate(), "idempotency_key": "enki"},
    )
    assert result["success"] and result["result"]["resource_id"] != ENKI
    with pytest.raises(EventPermissionError):  # and it cannot reach the pinned slot
        await provision(s, body())


@pytest.mark.asyncio
async def test_nested_invocation_requires_an_explicit_trusted_line() -> None:
    async def genesis_flow(synapse: Any, client: Any) -> Any:
        return await client.request(STORE_CREATE_PROVISIONED_SIGNAL, synapse.payload)

    for lines, allowed in (
        ((), False),
        ((TrustedLine("genesis.enki", STORE_CREATE_PROVISIONED_SIGNAL),), True),
    ):
        s = stack(grants={(TENANT, GENESIS): {"genesis.enki"}}, trusted_lines=lines)
        s.registry.subscribe("genesis.enki", provider="genesis.test")(genesis_flow)
        if allowed:
            assert (await s.client.request("genesis.enki", body()))["success"]
        else:
            with pytest.raises(EventPermissionError):
                await s.client.request("genesis.enki", body())
            assert s.persistence.documents == []


def test_registration_is_exact_versioned_and_grants_nothing() -> None:
    s = stack(grants={})
    binding = s.registry.resolve(STORE_CREATE_PROVISIONED_SIGNAL, STORE_CREATE_PROVISIONED_VERSION)
    assert (binding.provider, binding.version) == (STORE_PROVIDER, "1.0.0")
    assert s.registry.get_info(STORE_CREATE_PROVISIONED_SIGNAL).metadata["access"] == "system"


# --- Policy parsing and bootstrap registration --------------------------------------------


def _entry(**override: Any) -> dict[str, Any]:
    return {**ENKI_SLOT.model_dump(), **override}


@pytest.mark.parametrize(
    "items",
    [
        [_entry(), _entry()],  # duplicate slot
        [_entry(), _entry(slot="enki_two")],  # duplicate agent ID
        [_entry(agent_id=GENESIS)],  # provisioner provisioning itself
        [_entry(agent_id="")],
        [_entry(tenant_id=" ")],
        [_entry(slot="Enki")],
        [_entry(ownership="system")],  # system ownership is not expressible
        [_entry(runtime_version="v3")],
        [_entry(subject_id="not-the-approval-owner")],
        {"enki": _entry()},
    ],
)
def test_policy_rejects_ambiguous_or_broadened_provisioning(items: Any) -> None:
    from src.domains.agentverse.dependencies.genesis_bootstrap import _provisions

    approvals = {(TENANT, ENKI): AgentActivationApproval(TENANT, ENKI, OWNER, True)}
    with pytest.raises((ValueError, TypeError)):
        _provisions(items, approvals)


def test_policy_accepts_the_pinned_enki_entry() -> None:
    from src.domains.agentverse.dependencies.genesis_bootstrap import _provisions

    assert _provisions([_entry()], {}) == {"enki": ENKI_SLOT}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("grants", "provisioned", "registered"),
    [
        ({STORE_CREATE_PROVISIONED_SIGNAL}, [], False),  # grant without a pinned identity
        ({"chat.context"}, [_entry()], False),  # pinned identity without a grant
        ({STORE_CREATE_PROVISIONED_SIGNAL}, [_entry()], True),
    ],
)
async def test_bootstrap_registers_only_with_grant_and_pinned_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    grants: set,
    provisioned: list,
    registered: bool,
) -> None:
    from fastapi import FastAPI

    from src.base.services.jwt_service import JWTService
    from src.domains.agentverse import registries
    from src.domains.agentverse.dependencies.genesis_bootstrap import (
        build_genesis_security,
        configure_genesis_transport,
    )

    policy_file = tmp_path / "policy.json"
    policy_file.write_text(
        json.dumps(
            {
                "grants": [{"tenant_id": TENANT, "agent_id": GENESIS, "invoke": sorted(grants)}],
                "provisioned": provisioned,
            }
        )
    )
    monkeypatch.delenv("GENESIS_LOCAL_ENABLED", raising=False)
    monkeypatch.setenv("GENESIS_SECURITY_POLICY_FILE", str(policy_file))
    monkeypatch.setenv("GENESIS_SESSIONS_ENABLED", "true")
    registry = SynapticRegistry()
    monkeypatch.setattr(registries, "synaptic_registry_instance", registry)
    container = SimpleNamespace(
        redis=SimpleNamespace(
            redis_repository=AsyncMock(return_value=SimpleNamespace(eval=AsyncMock()))
        ),
        services=SimpleNamespace(jwt_service=lambda: JWTService()),
    )
    bindings = build_genesis_security(container)["genesis_security"]
    tool = stack().capability._tool
    app = FastAPI()
    app.container = SimpleNamespace(  # type: ignore[attr-defined]
        agentverse=SimpleNamespace(
            utu_authority=Mock(
                side_effect=lambda: bindings.sessions.bind_revocation_handler(AsyncMock())
            ),
            store=lambda: SimpleNamespace(tool=lambda: tool),
        )
    )
    configure_genesis_transport(app, bindings)
    await app.state.start_genesis_sessions()
    await app.state.close_genesis_sessions()
    assert (STORE_CREATE_PROVISIONED_SIGNAL in registry) is registered
    assert STORE_CREATE_SIGNAL not in registry  # the provisioned grant never registers it


def test_checked_in_local_policy_pins_only_enki_for_genesis() -> None:
    from src.domains.agentverse.dependencies import genesis_local
    from src.domains.agentverse.dependencies.genesis_bootstrap import _provisions

    config = json.loads(Path("config/genesis.local.json").read_text())
    approvals = {
        (a["tenant_id"], a["agent_id"]): AgentActivationApproval(**a) for a in config["approvals"]
    }
    assert _provisions(config["provisioned"], approvals) == {"enki": genesis_local.ENKI_PROVISION}
    grants = {g["agent_id"]: set(g["invoke"]) for g in config["grants"]}
    assert grants[genesis_local.GENESIS] == GENESIS_GRANTS
    assert STORE_CREATE_SIGNAL not in set().union(*grants.values())
    assert config["trusted_lines"] == []


# --- End to end and V2 compatibility ----------------------------------------------------


async def _validated_composed_enki() -> dict[str, Any]:
    from pydantic import BaseModel

    registries = {
        kind: Registry(kind) for kind in ("agent", "role", "personality", "rulepack", "tool")
    }
    for kind, key in (("agent", "chat"), ("role", "general"), ("personality", "empty_eva")):
        registries[kind].register(
            name=key, key=key, component=object, metadata={"ownership": "global"}
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
            "prompt": "You are Enki, keeper of CAOS agents.",
        },
        requester=User(id=OWNER, tenant_id=TENANT),
    )
    assert validation.valid, validation.issues

    class Llm(BaseModel):
        async def get_response_async(self, **_: Any) -> str:  # pragma: no cover
            raise AssertionError

    factory = ClientFactory({"llm": {"registered-model": Llm()}}, {"llm": "registered-model"})
    composed = AgentComposer(catalog=lambda: factory).compose(
        validation,
        context=CompositionContext.from_permit(ExecutionPermit(KEY, ("p",))),
        policy=CURRENT_V2_POLICY,
    )
    assert composed.composed, composed.issues
    return composed.candidate


@pytest.mark.asyncio
async def test_validation_composition_and_provisioned_store_create_enki() -> None:
    s = stack()
    result = await provision(s, body(await _validated_composed_enki()))
    assert result["success"], result
    (document,) = s.persistence.documents
    assert (document["id"], document["runtime_version"], document["llm_type"]) == (
        ENKI,
        "v2",
        "registered-model",
    )


@pytest.mark.asyncio
async def test_stored_enki_satisfies_khepri_activation_preconditions() -> None:
    s = stack()
    await provision(s, body())
    (document,) = s.persistence.documents
    enki_key = AgentInstanceKey(TENANT, OWNER, ENKI, "operator-session")
    session = VerifiedAgentSession(TENANT, OWNER, "operator-session", {ENKI}, active=True)
    resolver = AgentPrincipalResolver(
        definitions=SimpleNamespace(find_by_id=AsyncMock(return_value=document)),
        sessions=SimpleNamespace(get_session=AsyncMock(return_value=session)),
        authorization=SynapticAuthorization(
            grants={(TENANT, ENKI): SynapticGrants(["chat.context", "chat.response"])}
        ),
        approvals={(TENANT, ENKI): AgentActivationApproval(TENANT, ENKI, OWNER, True)},
    )
    principal = await resolver(enki_key)
    assert (principal.tenant_id, principal.agent_id) == (TENANT, ENKI)
    # Another user, even in the same tenant, is not the approved owner.
    other = VerifiedAgentSession(TENANT, "admin-2", "s2", {ENKI}, active=True)
    resolver._sessions = SimpleNamespace(get_session=AsyncMock(return_value=other))
    with pytest.raises(EventPermissionError):
        await resolver(AgentInstanceKey(TENANT, "admin-2", ENKI, "s2"))


@pytest.mark.asyncio
async def test_stored_enki_is_visible_to_its_operator_and_superadmins_only() -> None:
    s = stack()
    await provision(s, body())
    (document,) = s.persistence.documents
    agent = Agent.model_validate(document)

    def readable(user: User) -> bool:
        return UTUAuthority.can_access_resource(
            user, document, operation="DISCOVER"
        ) and agent_access.can_read(user, agent, operation="DISCOVER")

    assert readable(User(id=OWNER, tenant_id=TENANT))
    assert readable(User(id="root", tenant_id=TENANT, roles=[agent_access.SUPERADMIN_ROLE]))
    assert not readable(User(id="member", tenant_id=TENANT))
    assert not readable(User(id=OWNER, tenant_id="tenant-b"))
