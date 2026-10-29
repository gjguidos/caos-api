"""``store.create`` through real CPBX/UTU, the Store/Factory chain and MongoDB."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
import pytest_asyncio
from dependency_injector import providers
from pymongo.errors import AutoReconnect, DuplicateKeyError, OperationFailure

from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.agents.lifecycle.agent_principal_resolver import (
    AgentPrincipalResolver,
)
from src.domains.agentverse.entities.agent import Agent
from src.domains.agentverse.ncsc.authorization import SynapticAuthorization
from src.domains.agentverse.ncsc.cpbx import SynapticExchange
from src.domains.agentverse.ncsc.models import SynapticGrants, TrustedLine
from src.domains.agentverse.registries.synaptic_registry import SynapticRegistry
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey
from src.domains.agentverse.security.revocation import ExecutionPermit, policy_version
from src.domains.agentverse.security.utu import AuthorizedAgentPrincipal, UTUAuthority
from src.domains.agentverse.services.db_service import DBService
from src.domains.agentverse.tools.store_tool import StoreTool, register_store_create
from src.domains.agentverse.tools.store_tool.dependencies.di_container import (
    StoreToolContainer,
)
from src.domains.agentverse.tools.store_tool.models import (
    AmbiguousStoreWrite,
    StoreConflict,
)
from src.domains.agentverse.tools.store_tool.persistence.agent_definition_repository import (
    IDEMPOTENCY_INDEX,
    AgentDefinitionRepository,
)
from src.domains.agentverse.tools.store_tool.spec import (
    STORE_CREATE_SIGNAL,
    STORE_CREATE_VERSION,
    STORE_PROVIDER,
)
from src.domains.agentverse.tools.store_tool.store.factory import (
    build_agent_definition_store,
    build_approved_stores,
)

TENANT, OWNER, CALLER = "tenant-a", "owner-1", "genesis-caller"
KEY = AgentInstanceKey(TENANT, OWNER, CALLER, "secret-session-id")


def definition(**overrides: Any) -> dict[str, Any]:
    value: dict[str, Any] = {
        "runtime_version": "v2",
        "name": "Adam",
        "system_name": "adam",
        "type": "chat",
        "role_name": "assistant",
        "personality_key": "default",
        "prompt": "You are Adam.",
        "chat_url": "/chat",
        "llm_type": "openai",
        "audio_type": "none",
        "db_type": None,
        "cache_type": None,
        "knowledge_db_type": None,
        "messaging_type": None,
        "dna_sequence": "ACGT",
        "rule_pack_ids": ["core"],
        "visibility": "private",
    }
    value.update(overrides)
    return value


def request(payload: dict[str, Any] | None = None, *, key: str = "create-adam-1") -> dict[str, Any]:
    return {
        "resource_type": "agent",
        "payload": definition() if payload is None else payload,
        "idempotency_key": key,
    }


# --- Fakes -------------------------------------------------------------------------


class FakeCollection:
    """Enforces the unique partial scope index like MongoDB; failure hooks for tests."""

    def __init__(self) -> None:
        self.documents: list[dict[str, Any]] = []
        self.indexes: dict[str, dict[str, Any]] = {}
        self.insert_error: Exception | None = None
        self.commit_then_fail = False
        self.hide_reads = False
        self.index_error: Exception | None = None

    async def create_index(self, keys: Any, **options: Any) -> str:
        if self.index_error is not None:
            raise self.index_error
        self.indexes[options["name"]] = {"keys": keys, **options}
        return options["name"]

    async def insert_one(self, document: dict[str, Any]) -> Any:
        if self.insert_error is not None:
            raise self.insert_error
        await asyncio.sleep(0)  # interleave callers; check-and-append below stays atomic
        scope = (document.get("store") or {}).get("idempotency_scope")
        if (
            self.indexes
            and scope is not None
            and any(
                (row.get("store") or {}).get("idempotency_scope") == scope for row in self.documents
            )
        ):
            raise DuplicateKeyError("dup", 11000, {"keyPattern": {"store.idempotency_scope": 1}})
        document["_id"] = uuid4().hex
        self.documents.append(dict(document))
        if self.commit_then_fail:
            raise AutoReconnect("connection lost after commit")
        return SimpleNamespace(inserted_id=document["_id"])

    async def find_one(self, query: dict[str, Any], projection: Any = None) -> Any:
        if self.hide_reads:
            return None
        ((_, value),) = query.items()
        for row in self.documents:
            if (row.get("store") or {}).get("idempotency_scope") == value:
                return {k: v for k, v in row.items() if k != "_id"}
        return None


class FakeMongoRepository:
    def __init__(self) -> None:
        self.collection = FakeCollection()
        self.names: list[str] = []

    async def get_collection(self, name: str) -> FakeCollection:
        self.names.append(name)
        return self.collection


def authority(
    *, grants: set[str], trusted_lines: tuple[TrustedLine, ...] = (), stale: bool = False
) -> tuple[UTUAuthority, tuple[str, ...], Any]:
    policy = SynapticAuthorization(
        grants={(TENANT, CALLER): SynapticGrants(frozenset(grants))},
        trusted_lines=trusted_lines,
    )
    current = (policy_version(policy), "incarnation", "0", "0")
    sessions = SimpleNamespace(get_session=AsyncMock(), bind_revocation_handler=Mock())
    resolver = AgentPrincipalResolver(
        definitions=SimpleNamespace(find_by_id=AsyncMock()),
        sessions=sessions,
        authorization=policy,
        approvals={},
    )
    revocations = SimpleNamespace(
        read=AsyncMock(return_value=("old", "incarnation", "0", "0") if stale else current)
    )
    return (
        UTUAuthority(resolver=resolver, authorization=policy, revocations=revocations),
        current,
        revocations,
    )


def stack(
    repository: Any = None,
    *,
    grants: set[str] = frozenset({STORE_CREATE_SIGNAL}),
    collection: str | None = None,
    **kwargs: Any,
) -> SimpleNamespace:
    """Production composition: StoreToolContainer + SynapticRegistry + CPBX + UTU."""
    utu, versions, revocations = authority(grants=set(grants), **kwargs)
    repository = repository or FakeMongoRepository()
    container = StoreToolContainer(
        mongodb_repository=providers.Object(repository), utu_authority=providers.Object(utu)
    )
    if collection is not None:
        # Isolated real-Mongo collection; production uses DBService.AGENTS_COL.
        container.agent_definition_repository.override(
            providers.Singleton(AgentDefinitionRepository, repository, collection)
        )
    registry = SynapticRegistry()
    binding = register_store_create(registry, container.tool())
    exchange = SynapticExchange(registry=registry, authorization=utu)
    client = exchange.bind(AuthorizedAgentPrincipal(CALLER, TENANT, ExecutionPermit(KEY, versions)))
    return SimpleNamespace(
        client=client,
        exchange=exchange,
        repository=repository,
        container=container,
        binding=binding,
        revocations=revocations,
        versions=versions,
        registry=registry,
    )


async def store(s: SimpleNamespace, body: dict[str, Any]) -> dict[str, Any]:
    return await s.client.request(STORE_CREATE_SIGNAL, body)


def documents(s: SimpleNamespace) -> list[dict[str, Any]]:
    return s.repository.collection.documents


# --- 1, 2, 13: authorized creation and persisted structure --------------------------


@pytest.mark.asyncio
async def test_authorized_create_persists_server_owned_v2_definition() -> None:
    s = stack()
    result = await store(s, request())
    assert result["success"] is True and result["error"] is None
    body = result["result"]
    assert body["resource_type"] == "agent" and body["created"] is True
    (document,) = documents(s)
    assert document["id"] == body["resource_id"]
    assert (document["ownership"], document["tenant_id"], document["creator"]) == (
        "tenant",
        TENANT,
        OWNER,
    )
    assert document["exposure"] == "public"
    # Explicit V2 contract kept verbatim: never silently downgraded or activated.
    assert document["runtime_version"] == "v2"
    assert (document["role_name"], document["personality_key"], document["rule_pack_ids"]) == (
        "assistant",
        "default",
        ["core"],
    )
    restored = Agent.model_validate(document)
    assert (
        restored.model_dump()
        == Agent.model_validate(
            {
                **definition(),
                **{k: document[k] for k in ("id", "ownership", "tenant_id", "creator", "exposure")},
            }
        ).model_dump()
    )
    for field in ("created_at", "updated_at", "created", "modified"):
        assert field in document
    record = document["store"]
    assert record["idempotency_scope"] and record["request_digest"] == body["request_digest"]
    assert record["authorization"]["tenant_id"] == TENANT
    assert record["authorization"]["principal_id"] == CALLER
    assert record["authorization"]["subject_id"] == OWNER
    assert "secret-session-id" not in repr(document) and "incarnation" not in repr(document)


@pytest.mark.asyncio
async def test_v1_definitions_keep_their_version_and_version_is_never_defaulted() -> None:
    s = stack()
    v1 = await store(s, request(definition(runtime_version="v1"), key="v1"))
    assert v1["success"] and documents(s)[0]["runtime_version"] == "v1"
    payload = definition()
    del payload["runtime_version"]
    missing = await store(s, request(payload, key="missing"))
    assert missing["meta"]["code"] == "STORE_INVALID_PAYLOAD"
    assert len(documents(s)) == 1


def test_registration_is_exact_versioned_and_grants_nothing() -> None:
    s = stack(grants=set())
    binding = s.registry.resolve(STORE_CREATE_SIGNAL, STORE_CREATE_VERSION)
    assert (binding.signal, binding.provider, binding.version) == (
        STORE_CREATE_SIGNAL,
        STORE_PROVIDER,
        STORE_CREATE_VERSION,
    )
    info = s.registry.get_info(STORE_CREATE_SIGNAL)
    assert info is not None and info.metadata["access"] == "system"


# --- 3, 4: Store/Factory dependency injection and shared repository -----------------


def test_container_builds_the_chain_over_the_shared_repository() -> None:
    s = stack()
    container = s.container
    repository = container.agent_definition_repository()
    assert repository.repository is s.repository  # no new client or pool
    assert repository.collection_name == DBService.AGENTS_COL
    store_ = container.agent_definition_store()
    assert store_.persistence is repository
    assert dict(container.approved_stores()) == {"agent": store_}
    assert container.tool() is container.tool()
    with pytest.raises(TypeError):
        container.approved_stores()["workflow"] = store_  # type: ignore[index]


def test_agentverse_container_shares_application_repository_and_utu() -> None:
    from src.domains.agentverse.dependencies.di_container import AgentverseContainer

    container = AgentverseContainer()
    shared = FakeMongoRepository()
    container.mongodb_repository.override(providers.Object(shared))
    container.redis_repository.override(providers.Object(Mock()))
    store_container = container.store()
    assert store_container.agent_definition_repository().repository is shared
    assert store_container.tool()._authority is container.utu_authority()


@pytest.mark.asyncio
async def test_store_is_independently_usable_with_a_trusted_context() -> None:
    repository = AgentDefinitionRepository(FakeMongoRepository())
    agent_store = build_agent_definition_store(persistence=repository)
    assert build_approved_stores(agent_store=agent_store)["agent"] is agent_store
    from src.domains.agentverse.operations.admission import permit_provenance
    from src.domains.agentverse.tools.store_tool.models import StoreWriteContext

    verify = AsyncMock()
    context = StoreWriteContext(
        provenance=permit_provenance(ExecutionPermit(KEY, ("p", "i", "0", "0")), "store.create"),
        verify=verify,
    )
    result = await agent_store.create(definition(), idempotency_key="k", context=context)
    assert result.created and verify.await_count == 1


# --- 5: authorization ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_invocation_without_store_grant_is_denied_before_any_write() -> None:
    s = stack(grants={"chat.context"})
    with pytest.raises(EventPermissionError):
        await store(s, request())
    assert documents(s) == [] and s.repository.names == []


@pytest.mark.asyncio
async def test_superseded_permit_is_denied() -> None:
    s = stack(stale=True)
    with pytest.raises(EventPermissionError):
        await store(s, request())
    assert documents(s) == []


@pytest.mark.asyncio
async def test_client_without_utu_permit_is_denied() -> None:
    s = stack()
    from src.base.events.v2.policy import EventPrincipal

    client = s.exchange.bind(EventPrincipal(CALLER, TENANT))
    with pytest.raises(EventPermissionError):
        await client.request(STORE_CREATE_SIGNAL, request())
    assert documents(s) == []


@pytest.mark.asyncio
async def test_direct_handler_call_with_forged_context_is_denied() -> None:
    s = stack()
    from src.domains.agentverse.ncsc.models import Synapse

    tool: StoreTool = s.container.tool()
    forged = SimpleNamespace(execution_permit=ExecutionPermit(KEY, s.versions))
    synapse = Synapse(STORE_CREATE_SIGNAL, "someone-else", STORE_PROVIDER, TENANT, request())
    with pytest.raises(EventPermissionError):
        await tool.create(synapse, forged)  # type: ignore[arg-type]
    no_permit = SimpleNamespace(execution_permit=None)
    synapse = Synapse(STORE_CREATE_SIGNAL, CALLER, STORE_PROVIDER, TENANT, request())
    with pytest.raises(EventPermissionError):
        await tool.create(synapse, no_permit)  # type: ignore[arg-type]
    assert documents(s) == []


@pytest.mark.asyncio
async def test_authority_revoked_before_the_write_boundary_prevents_the_write() -> None:
    s = stack()
    current = s.versions
    calls = 0

    async def read(_: Any) -> tuple[str, ...]:
        nonlocal calls
        calls += 1
        # CPBX entry and tool entry see live authority; the write-boundary check does not.
        return current if calls <= 2 else ("revoked", "incarnation", "0", "0")

    s.revocations.read = read
    with pytest.raises(EventPermissionError):
        await store(s, request())
    assert documents(s) == []


@pytest.mark.asyncio
async def test_nested_invocation_requires_an_explicit_trusted_line() -> None:
    async def workflow(synapse: Any, client: Any) -> Any:
        return await client.request(STORE_CREATE_SIGNAL, synapse.payload)

    for lines, allowed in (
        ((), False),
        ((TrustedLine("workflow.genesis", STORE_CREATE_SIGNAL),), True),
    ):
        s = stack(grants={"workflow.genesis"}, trusted_lines=lines)
        s.registry.subscribe("workflow.genesis", provider="workflow.test")(workflow)
        if allowed:
            assert (await s.client.request("workflow.genesis", request()))["success"]
        else:
            with pytest.raises(EventPermissionError):
                await s.client.request("workflow.genesis", request())
            assert documents(s) == []


# --- 6, 7: tenant and ownership spoofing --------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "override",
    [
        {"tenant_id": "tenant-b"},
        {"ownership": "system"},
        {"ownership": "global", "tenant_id": None},
        {"creator": "someone-else"},
        {"exposure": "internal"},
        {"id": "chosen-agent-id"},
    ],
)
async def test_tenant_ownership_and_identity_spoofing_is_rejected(override: dict[str, Any]) -> None:
    s = stack()
    result = await store(s, request(definition(**override)))
    assert result["success"] is False
    assert result["meta"]["code"] == "STORE_OWNERSHIP_VIOLATION"
    assert documents(s) == []


@pytest.mark.asyncio
async def test_payload_may_repeat_exact_server_values() -> None:
    s = stack()
    first = await store(s, request())
    agent_id = first["result"]["resource_id"]
    repeated = definition(
        id=agent_id, ownership="tenant", tenant_id=TENANT, creator=OWNER, exposure="public"
    )
    replay = await store(s, request(repeated))
    assert replay["result"] == {**first["result"], "created": False}


@pytest.mark.asyncio
async def test_system_write_context_is_not_enabled() -> None:
    from src.domains.agentverse.operations.admission import permit_provenance
    from src.domains.agentverse.tools.store_tool.models import (
        StoreOwnershipViolation,
        StoreWriteContext,
    )

    repository = FakeMongoRepository()
    agent_store = build_agent_definition_store(persistence=AgentDefinitionRepository(repository))
    context = StoreWriteContext(
        provenance=permit_provenance(ExecutionPermit(KEY, ("p", "i", "0", "0")), "store.create"),
        verify=AsyncMock(),
        ownership="system",
    )
    with pytest.raises(StoreOwnershipViolation):
        await agent_store.create(definition(), idempotency_key="k", context=context)
    assert repository.collection.documents == []


# --- 8: resource types and payloads ---------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("resource_type", ["workflow", "coll_agents", "", None])
async def test_unapproved_resource_types_are_rejected(resource_type: Any) -> None:
    s = stack()
    result = await store(s, {**request(), "resource_type": resource_type})
    assert result["meta"]["code"] == "STORE_UNSUPPORTED_RESOURCE"
    assert documents(s) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {**request(), "collection_name": "coll_users"},
        {**request(), "filter": {"id": "x"}},
        {**request(), "idempotency_key": ""},
        {**request(), "idempotency_key": " padded "},
        {k: v for k, v in request().items() if k != "idempotency_key"},
        {**request(), "payload": "not-an-object"},
        request(definition(**{"$set": {"ownership": "system"}})),
        request(definition(collection="coll_users")),
        request(definition(commbridge={"x": 1})),
        request(definition(genesis_local_seed=True)),
        request({k: v for k, v in definition().items() if k != "prompt"}),
        request(definition(runtime_version="v3")),
        request(definition(name=7)),
    ],
)
async def test_invalid_envelopes_and_definitions_are_rejected(body: dict[str, Any]) -> None:
    s = stack()
    result = await store(s, body)
    assert result["success"] is False
    assert result["meta"] == {"code": "STORE_INVALID_PAYLOAD", "retryable": False}
    assert documents(s) == []


# --- 9, 10, 11: idempotency ------------------------------------------------------------


@pytest.mark.asyncio
async def test_identical_replay_returns_the_existing_result() -> None:
    s = stack()
    first = await store(s, request())
    replay = await store(s, request())
    assert replay["success"] and replay["result"] == {**first["result"], "created": False}
    assert len(documents(s)) == 1


@pytest.mark.asyncio
async def test_conflicting_replay_fails_without_modifying_the_agent() -> None:
    s = stack()
    await store(s, request())
    before = [dict(row) for row in documents(s)]
    conflict = await store(s, request(definition(prompt="Different")))
    assert conflict["meta"] == {"code": "STORE_IDEMPOTENCY_CONFLICT", "retryable": False}
    assert documents(s) == before


@pytest.mark.asyncio
async def test_scopes_are_independent_per_key() -> None:
    s = stack()
    first = await store(s, request(key="a"))
    second = await store(s, request(key="b"))
    assert first["result"]["resource_id"] != second["result"]["resource_id"]
    assert len(documents(s)) == 2


@pytest.mark.asyncio
async def test_concurrent_duplicate_requests_create_exactly_one() -> None:
    s = stack()
    results = await asyncio.gather(*(store(s, request()) for _ in range(10)))
    assert [r["result"]["created"] for r in results].count(True) == 1
    assert len({r["result"]["resource_id"] for r in results}) == 1
    assert len(documents(s)) == 1


# --- 12: database failures and uncertain outcomes -------------------------------------


@pytest.mark.asyncio
async def test_lost_acknowledgement_is_ambiguous_and_resolved_by_retry() -> None:
    s = stack()
    s.repository.collection.commit_then_fail = True
    first = await store(s, request())
    assert first["meta"] == {"code": "STORE_AMBIGUOUS_WRITE", "retryable": True}
    s.repository.collection.commit_then_fail = False
    retry = await store(s, request())
    assert retry["success"] and retry["result"]["created"] is False
    assert len(documents(s)) == 1


@pytest.mark.asyncio
async def test_failed_write_is_ambiguous_and_retry_creates() -> None:
    s = stack()
    s.repository.collection.insert_error = AutoReconnect("down")
    assert (await store(s, request()))["meta"]["code"] == "STORE_AMBIGUOUS_WRITE"
    s.repository.collection.insert_error = None
    assert (await store(s, request()))["result"]["created"] is True


@pytest.mark.asyncio
async def test_duplicate_not_yet_visible_is_ambiguous() -> None:
    repository = FakeMongoRepository()
    adapter = AgentDefinitionRepository(repository)
    document = {"id": "a", "store": {"idempotency_scope": "s"}}
    await adapter.insert_once(dict(document))
    repository.collection.hide_reads = True
    with pytest.raises(AmbiguousStoreWrite):
        await adapter.insert_once(dict(document))


@pytest.mark.asyncio
async def test_duplicate_on_another_unique_index_is_a_conflict_not_a_replay() -> None:
    repository = FakeMongoRepository()
    repository.collection.insert_error = DuplicateKeyError("dup", 11000, {"keyPattern": {"_id": 1}})
    with pytest.raises(StoreConflict):
        await AgentDefinitionRepository(repository).insert_once(
            {"id": "a", "store": {"idempotency_scope": "s"}}
        )


@pytest.mark.asyncio
async def test_missing_index_guarantee_fails_closed_before_writing() -> None:
    s = stack()
    s.repository.collection.index_error = OperationFailure("partial indexes unsupported")
    result = await store(s, request())
    assert result["meta"] == {"code": "STORE_UNAVAILABLE", "retryable": True}
    assert documents(s) == []


@pytest.mark.asyncio
async def test_index_is_unique_partial_on_the_store_scope_only() -> None:
    repository = FakeMongoRepository()
    await AgentDefinitionRepository(repository).ensure_indexes()
    index = repository.collection.indexes[IDEMPOTENCY_INDEX]
    assert index["unique"] is True
    assert index["partialFilterExpression"] == {"store.idempotency_scope": {"$exists": True}}
    assert repository.names == [DBService.AGENTS_COL]


# --- Real MongoDB (also 11, 12 and 14) ------------------------------------------------


@pytest_asyncio.fixture
async def mongo() -> AsyncIterator[tuple[Any, str]]:
    if os.environ.get("OPS2_MONGO_INTEGRATION") != "1":
        pytest.skip("Set OPS2_MONGO_INTEGRATION=1 for real Mongo store tests")
    from src.base.infrastructure.db.mongoDB.isolation import isolated_mongo_repository

    # A disposable database, dropped whole: never the configured application DB.
    async with isolated_mongo_repository("store") as infrastructure:
        yield infrastructure, f"store_tool_test_{uuid4().hex}"


def mongo_stack(mongo: tuple[Any, str]) -> SimpleNamespace:
    infrastructure, name = mongo
    return stack(infrastructure, collection=name)


@pytest.mark.asyncio
async def test_mongo_create_replay_conflict_and_restore(mongo: Any) -> None:
    s = mongo_stack(mongo)
    first = await store(s, request())
    assert first["result"]["created"] is True
    assert (await store(s, request()))["result"]["created"] is False
    conflict = await store(s, request(definition(prompt="Other")))
    assert conflict["meta"]["code"] == "STORE_IDEMPOTENCY_CONFLICT"
    infrastructure, name = mongo
    rows = await (await infrastructure.get_collection(name)).find({}).to_list(length=10)
    assert len(rows) == 1
    restored = Agent.model_validate(rows[0])
    assert (restored.runtime_version, restored.ownership, restored.tenant_id) == (
        "v2",
        "tenant",
        TENANT,
    )
    assert rows[0]["prompt"] == "You are Adam."  # the conflicting retry modified nothing


@pytest.mark.asyncio
async def test_mongo_concurrent_duplicates_across_fresh_adapters(mongo: Any) -> None:
    stacks = [mongo_stack(mongo) for _ in range(4)]
    results = await asyncio.gather(*(store(stacks[i % 4], request()) for i in range(16)))
    assert [r["result"]["created"] for r in results].count(True) == 1
    infrastructure, name = mongo
    assert await (await infrastructure.get_collection(name)).count_documents({}) == 1


@pytest.mark.asyncio
async def test_mongo_index_leaves_v1_store_agent_unconstrained(mongo: Any) -> None:
    s = mongo_stack(mongo)
    await store(s, request())
    infrastructure, name = mongo
    db = DBService(infrastructure)
    db.AGENTS_COL = name  # type: ignore[misc]
    emitter = SimpleNamespace(log=AsyncMock())
    for _ in range(2):
        agent = Agent.model_validate({**definition(runtime_version="v1"), "id": uuid4().hex})
        await db.store_agent(emitter, agent)
    collection = await infrastructure.get_collection(name)
    assert await collection.count_documents({}) == 3
    assert await collection.count_documents({"store": {"$exists": False}}) == 2
    indexes = await collection.index_information()
    assert indexes[IDEMPOTENCY_INDEX]["unique"] is True


# --- Trusted bootstrap registration ----------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "grants,lines,registered",
    [
        ({"chat.context"}, (), False),
        ({STORE_CREATE_SIGNAL}, (), True),
        ({"workflow.genesis"}, (TrustedLine("workflow.genesis", STORE_CREATE_SIGNAL),), True),
    ],
)
async def test_bootstrap_registers_store_create_only_for_reviewed_policy(
    monkeypatch: pytest.MonkeyPatch, grants: set[str], lines: tuple[Any, ...], registered: bool
) -> None:
    from dataclasses import replace

    from fastapi import FastAPI

    from src.base.services.jwt_service import JWTService
    from src.domains.agentverse import registries
    from src.domains.agentverse.dependencies.genesis_bootstrap import (
        build_genesis_security,
        configure_genesis_transport,
    )

    monkeypatch.delenv("GENESIS_LOCAL_ENABLED", raising=False)
    monkeypatch.delenv("GENESIS_SECURITY_POLICY_FILE", raising=False)
    monkeypatch.setenv("GENESIS_SESSIONS_ENABLED", "true")
    registry = SynapticRegistry()
    monkeypatch.setattr(registries, "synaptic_registry_instance", registry)
    container = SimpleNamespace(
        redis=SimpleNamespace(
            redis_repository=AsyncMock(return_value=SimpleNamespace(eval=AsyncMock()))
        ),
        services=SimpleNamespace(jwt_service=lambda: JWTService()),
    )
    bindings = replace(
        build_genesis_security(container)["genesis_security"],
        authorization=SynapticAuthorization(
            grants={(TENANT, CALLER): SynapticGrants(frozenset(grants))}, trusted_lines=lines
        ),
    )
    tool = stack().container.tool()
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
    assert (STORE_CREATE_SIGNAL in registry) is registered
    if registered:
        assert registry.resolve(STORE_CREATE_SIGNAL, STORE_CREATE_VERSION).handler == tool.create
