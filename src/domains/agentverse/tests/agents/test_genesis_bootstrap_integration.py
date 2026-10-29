"""Automatic local Enki Genesis bootstrap against real Mongo and disposable Redis.

Every app here is the production composition (``build_genesis_security`` →
``initialize_domains`` → ``configure_genesis_transport``) sharing one disposable Mongo
database and one disposable Redis namespace, so restarts and concurrent starts are
real. No LLM call is permitted; Enki, JOSHUA and Genesis are never activated.
"""

from __future__ import annotations

import asyncio
import importlib
import os
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from dependency_injector import providers
from fastapi import FastAPI
from redis.asyncio import Redis

from src.base.config.config import settings
from src.base.dependencies.di_container import Container
from src.base.infrastructure.ai.client_factory import ClientFactory
from src.base.infrastructure.db.mongoDB.isolation import (
    disposable_database_name,
    require_test_database,
)
from src.base.infrastructure.db.mongoDB.mongo_client import MongoDBClient
from src.base.operations.v2.models import OperationRecord, OperationState
from src.base.repositories.redis_impl import RedisRepositoryImpl
from src.domains import initialize_domains
from src.domains.agentverse import registries as registry_instances
from src.domains.agentverse.agents.chat_agent.chat_agent import ChatAgent
from src.domains.agentverse.agents.genesis import (
    OPERATION_KIND,
    OPERATION_PRODUCER,
    genesis_operation_id,
)
from src.domains.agentverse.agents.genesis.bootstrap import (
    GenesisBootstrapConflict,
    GenesisBootstrapError,
)
from src.domains.agentverse.agents.genesis.initiation import INITIATION_EVENT
from src.domains.agentverse.agents.genesis.models import GenesisProvisionRequest
from src.domains.agentverse.agents.roles.builtin.spec import role_spec
from src.domains.agentverse.agents.roles.enki.spec import role_spec as enki_role_spec
from src.domains.agentverse.agents.specs.chat_agent import chat_spec
from src.domains.agentverse.agents.specs.form_agent import form_agent_spec
from src.domains.agentverse.audit.repository import LogUnavailable
from src.domains.agentverse.dependencies.genesis_bootstrap import (
    build_genesis_security,
    configure_genesis_transport,
)
from src.domains.agentverse.dependencies.genesis_local import (
    ENKI,
    GENESIS,
    JOSHUA,
    OWNER,
    TENANT,
)
from src.domains.agentverse.personalities import (  # noqa: F401 - production registration
    empty_eva,
)
from src.domains.agentverse.personalities import enki as enki_soul  # noqa: F401
from src.domains.agentverse.personalities import thoth as thoth_soul  # noqa: F401
from src.domains.agentverse.registries.synaptic_registry import SynapticRegistry
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey
from src.domains.agentverse.security.revocation import ExecutionPermit

UNRELATED = {"id": "unrelated-agent", "name": "Unrelated", "type": "chat", "creator": "x"}


class NoInference:
    async def get_response_async(self, **kwargs):
        raise AssertionError("Genesis bootstrap must not perform inference")


@pytest_asyncio.fixture
async def world(monkeypatch):
    """Shared persistence; ``start()`` composes one more API process over it."""
    url = os.environ.get("UTU_TEST_REDIS_URL")
    if os.environ.get("OPS2_MONGO_INTEGRATION") != "1" or not url:
        pytest.skip("Requires real Mongo and disposable UTU_TEST_REDIS_URL")
    for key, value in {
        "GENESIS_SESSIONS_ENABLED": "true",
        "GENESIS_LOCAL_ENABLED": "true",
        "GENESIS_SECURITY_POLICY_FILE": "config/genesis.local.json",
        "AUTH_MODE": "local",
        "ENVIRONMENT": "development",
        "LOCAL_JWT_SECRET": "bootstrap-test-secret-that-is-at-least-32-characters",
    }.items():
        monkeypatch.setenv(key, value)
    for module in ("date_time_tool", "analyze_tool", "refine_tool", "evaluate_tool"):
        importlib.import_module("src.domains.agentverse.tools." + module)
    registry_instances.agent_registry_instance.bind_spec(chat_spec)(ChatAgent)
    registry_instances.agent_registry_instance.bind_spec(form_agent_spec)(ChatAgent)
    registry_instances.role_registry_instance.register_role_spec(role_spec)
    registry_instances.role_registry_instance.register_role_spec(enki_role_spec)
    mongo = MongoDBClient(
        settings.database.resolved_uri, require_test_database(disposable_database_name("g1boot"))
    )
    redis = Redis.from_url(url)
    session_keys = ["g1b:{" + uuid4().hex + "}" + suffix for suffix in (":records", ":due")]
    apps = []

    async def start():
        # Composition registers into the module registry; isolate it per process.
        registry = SynapticRegistry()
        monkeypatch.setattr(registry_instances, "synaptic_registry_instance", registry)
        container = Container()
        container.database.mongo_client.override(providers.Object(mongo))
        container.redis.redis_repository.override(providers.Object(RedisRepositoryImpl(redis)))
        security = build_genesis_security(container)
        sessions = security["genesis_security"].sessions
        sessions._keys = list(session_keys)
        container = initialize_domains(container, **security)
        container.agentverse.synaptic_exchange.add_kwargs(registry=registry)
        app = FastAPI()
        app.container = container
        configure_genesis_transport(app, security["genesis_security"])
        await app.state.start_genesis_sessions()
        # Lifespan builds the model catalog before the bootstrap hook runs.
        app.state.client_factory = ClientFactory({"llm": {"test": NoInference()}}, {"llm": "test"})
        process = SimpleNamespace(
            app=app,
            av=container.agentverse,
            sessions=sessions,
            registry=registry,
            bootstrap=app.state.bootstrap_genesis.__self__,
        )
        apps.append(process)
        return process

    async def leases():
        records = await redis.hvals(session_keys[0])
        import json

        return [json.loads(r) for r in records if r not in (b"revoked",)]

    try:
        yield SimpleNamespace(start=start, mongo=mongo, redis=redis, leases=leases)
    finally:
        for process in apps:
            await process.av.agent_lifecycle_service().shutdown()
            await process.sessions.close()
        await redis.delete(*session_keys)
        if apps:
            await redis.delete(apps[0].av.utu_authority()._store()._key(TENANT))
        if mongo.client is not None:
            await mongo.client.drop_database(mongo.db_name)
        await mongo.disconnect()
        await redis.aclose()


async def inventory(w):
    return {
        "agents": sorted(await w.mongo.db["coll_agents"].distinct("id")),
        "operations": await w.mongo.db["operations_v2"].count_documents({}),
        "logs": await w.mongo.db["coll_logs"].count_documents({}),
    }


def nothing_active(process):
    running = {key.agent_id for key in process.av.instance_registry_v2().keys()}  # noqa: SIM118
    assert not running & {ENKI, JOSHUA, GENESIS}


@pytest.mark.asyncio
async def test_empty_database_creates_exactly_one_sleeping_enki_with_bootstrap_provenance(world):
    w = world
    process = await w.start()
    assert await inventory(w) == {"agents": [], "operations": 0, "logs": 0}  # startup wrote nothing
    await w.mongo.db["coll_agents"].insert_one(dict(UNRELATED))
    assert await process.app.state.bootstrap_genesis() == "created"

    # No Adam, JOSHUA or Genesis documents; the unrelated Agent is preserved.
    assert (await inventory(w))["agents"] == sorted([ENKI, UNRELATED["id"]])
    assert await w.mongo.db["coll_agents"].count_documents(
        {"id": UNRELATED["id"], "name": "Unrelated"}
    )
    enki = await w.mongo.db["coll_agents"].find_one({"id": ENKI})
    assert (
        enki["type"],
        enki["role_name"],
        enki["personality_key"],
        enki["runtime_version"],
        enki["tenant_id"],
        enki["creator"],
    ) == ("form", "enki", "enki", "v2", TENANT, OWNER)
    store = enki["store"]["authorization"]
    assert (store["principal_id"], store["subject_id"], store["origin"]) == (
        GENESIS,
        OWNER,
        "bootstrap",
    )

    operations = await w.mongo.db["operations_v2"].find({}).to_list(None)
    assert len(operations) == 1
    snapshot = operations[0]["snapshot"]
    assert (snapshot["kind"], snapshot["state"]) == (OPERATION_KIND, "succeeded")
    records = await process.av.caos_log_service().history(TENANT, snapshot["correlation_id"])
    attribution = sorted(
        (r.event_type, r.actor.principal_id, r.actor.user_id, r.authorization.origin)
        for r in records
    )
    assert attribution == sorted(
        [
            (INITIATION_EVENT, JOSHUA, OWNER, "bootstrap"),
            ("genesis.validation.completed", GENESIS, OWNER, "bootstrap"),
            ("genesis.composition.completed", GENESIS, OWNER, "bootstrap"),
            ("genesis.store.completed", GENESIS, OWNER, "bootstrap"),
        ]
    )
    store_record = next(r for r in records if r.event_type == "genesis.store.completed")
    assert store_record.subject.resource_id == ENKI
    assert {str(r.operation_id) for r in records} == {snapshot["operation_id"]}

    # Enki sleeps; no citizen was cognitively activated; the lease is revoked.
    nothing_active(process)
    leases = [r for r in await w.leases() if r.get("origin") == "bootstrap"]
    assert len(leases) == 1 and leases[0]["status"] == "revoked"
    assert set(leases[0]["agents"]) == {JOSHUA, GENESIS}


@pytest.mark.asyncio
async def test_restart_leaves_enki_unchanged_without_new_audit_or_lease(world):
    w = world
    first = await w.start()
    assert await first.app.state.bootstrap_genesis() == "created"
    before_doc = await w.mongo.db["coll_agents"].find_one({"id": ENKI})
    before = (await inventory(w), await w.leases())
    for _ in range(2):
        restarted = await w.start()
        assert await restarted.app.state.bootstrap_genesis() == "present"
    assert await w.mongo.db["coll_agents"].find_one({"id": ENKI}) == before_doc
    assert (await inventory(w), await w.leases()) == before


@pytest.mark.asyncio
async def test_concurrent_starts_create_one_enki_through_one_operation(world):
    w = world
    processes = [await w.start() for _ in range(3)]
    for process in processes:
        process.bootstrap._sleep = lambda _: asyncio.sleep(0.05)
    results = await asyncio.gather(*(p.app.state.bootstrap_genesis() for p in processes))
    assert "created" in results and set(results) <= {"created", "present"}
    state = await inventory(w)
    assert state["agents"] == [ENKI]
    assert state["operations"] == 1
    # One execution fact per step; one initiation fact per lease that actually initiated.
    executed = await w.mongo.db["coll_logs"].count_documents(
        {"event_type": {"$ne": INITIATION_EVENT}}
    )
    assert executed == 3
    for process in processes:
        nothing_active(process)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "conflict",
    [
        {"type": "chat"},
        {"role_name": "general"},
        {"personality_key": "yoda"},
        {"personality_key": "empty_eva"},  # the pre-Soul Enki after its migration
        {"creator": "someone-else"},
        {"store": None},  # not created through the provisioned Store scope
    ],
)
async def test_incompatible_existing_enki_fails_closed_and_is_never_overwritten(world, conflict):
    w = world
    process = await w.start()
    assert await process.app.state.bootstrap_genesis() == "created"
    await w.mongo.db["coll_agents"].update_one({"id": ENKI}, {"$set": conflict})
    tampered = await w.mongo.db["coll_agents"].find_one({"id": ENKI})
    before = await inventory(w)
    restarted = await w.start()
    with pytest.raises(GenesisBootstrapConflict):
        await restarted.app.state.bootstrap_genesis()
    assert await w.mongo.db["coll_agents"].find_one({"id": ENKI}) == tampered
    assert await inventory(w) == before


@pytest.mark.asyncio
async def test_database_unavailable_is_not_missing_enki(world):
    w = world
    process = await w.start()

    async def unavailable(agent_id):
        raise ConnectionError("mongo unavailable")

    process.bootstrap._definitions.find_by_id = unavailable
    with pytest.raises(ConnectionError):
        await process.app.state.bootstrap_genesis()
    assert await inventory(w) == {"agents": [], "operations": 0, "logs": 0}
    assert await w.leases() == []  # no lease was opened


@pytest.mark.asyncio
async def test_audit_failure_fails_startup_after_its_deadline_then_recovers(world):
    w = world
    process = await w.start()
    log = process.av.caos_log_service()
    append = log.append

    async def unavailable(*args, **kwargs):
        raise LogUnavailable("seshat down")

    log.append = unavailable
    process.bootstrap._deadline = 0.5
    process.bootstrap._sleep = lambda _: asyncio.sleep(0)
    with pytest.raises(GenesisBootstrapError, match="deadline"):
        await process.app.state.bootstrap_genesis()
    # Mandatory audit precedes every effect: no operation, no definition.
    assert await inventory(w) == {"agents": [], "operations": 0, "logs": 0}
    assert all(r["status"] == "revoked" for r in await w.leases())
    log.append = append
    restarted = await w.start()
    assert await restarted.app.state.bootstrap_genesis() == "created"
    assert (await inventory(w))["agents"] == [ENKI]


@pytest.mark.asyncio
async def test_interrupted_operation_is_recovered_by_ops_v2_takeover(world):
    w = world
    process = await w.start()
    entry = process.bootstrap._entry
    request = GenesisProvisionRequest(slot=entry.slot, creation=dict(entry.creation))
    # Identity derivation only: the operation ID binds tenant, Genesis, owner and input.
    operation_id = genesis_operation_id(
        ExecutionPermit(AgentInstanceKey(TENANT, OWNER, GENESIS, "derive"), ("v",)), request
    )
    repository = process.app.container.operations.managed_operation_repository_v2()
    from datetime import UTC, datetime

    record = await repository.create(
        OperationRecord.create(
            kind=OPERATION_KIND,
            producer=OPERATION_PRODUCER,
            at=datetime.now(UTC),
            operation_id=operation_id,
            correlation_id=str(operation_id),
            causation_id=str(operation_id),
        )
    )
    # A process that crashed while holding the attempt: lease taken, never finished.
    await repository.activate(
        operation_id,
        expected_revision=record.snapshot.revision,
        expected_generation=record.execution.generation,
        attempt_id=uuid4(),
        owner_id="genesis:crashed",
        lease_ms=200,
        at=datetime.now(UTC),
        command_id=uuid4(),
    )
    process.bootstrap._sleep = lambda _: asyncio.sleep(0.3)
    assert await process.app.state.bootstrap_genesis() == "created"
    recovered = await repository.inspect(UUID(str(operation_id)))
    assert recovered.snapshot.state == OperationState.SUCCEEDED
    assert len(recovered.snapshot.attempts) == 2
    assert (await inventory(w))["agents"] == [ENKI]
