"""Real local G1.4 bootstrap, verified operator session and callable V2 endpoint.

Uses a disposable Mongo database and UTU_TEST_REDIS_URL (disposable Redis only).
No LLM call is permitted, and Enki is never activated.
"""

from __future__ import annotations

import asyncio
import importlib
import os
import secrets
import time
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from dependency_injector import providers
from fastapi import FastAPI
from jose import jwt
from redis.asyncio import Redis

from src.base.config.config import settings
from src.base.dependencies.di_container import Container
from src.base.entities.user import User
from src.base.events.v2.contracts import EventPermissionError
from src.base.infrastructure.ai.client_factory import ClientFactory
from src.base.infrastructure.db.mongoDB.mongo_client import MongoDBClient
from src.base.repositories.redis_impl import RedisRepositoryImpl
from src.domains import initialize_domains
from src.domains.agentverse import registries as registry_instances
from src.domains.agentverse.agents.chat_agent.chat_agent import ChatAgent
from src.domains.agentverse.agents.genesis.initiation import INITIATION_EVENT, SIGNAL
from src.domains.agentverse.agents.roles.builtin.spec import role_spec
from src.domains.agentverse.agents.roles.enki.spec import role_spec as enki_role_spec
from src.domains.agentverse.agents.specs.chat_agent import chat_spec
from src.domains.agentverse.agents.specs.form_agent import form_agent_spec
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
from src.domains.agentverse.interfaces.api.v2.genesis_agents import router as agent_router
from src.domains.agentverse.interfaces.api.v2.genesis_sessions import router as session_router
from src.domains.agentverse.personalities import empty_eva  # noqa: F401 - production registration
from src.domains.agentverse.registries.synaptic_registry import SynapticRegistry
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey


class NoInference:
    async def get_response_async(self, **kwargs):
        raise AssertionError("Genesis initiation must not perform inference")


@pytest_asyncio.fixture
async def local_app(monkeypatch):
    url = os.environ.get("UTU_TEST_REDIS_URL")
    if os.environ.get("OPS2_MONGO_INTEGRATION") != "1" or not url:
        pytest.skip("Requires real Mongo and disposable UTU_TEST_REDIS_URL")
    for key, value in {
        "GENESIS_SESSIONS_ENABLED": "true",
        "GENESIS_LOCAL_ENABLED": "true",
        "GENESIS_SECURITY_POLICY_FILE": "config/genesis.local.json",
        "AUTH_MODE": "local",
        "ENVIRONMENT": "development",
    }.items():
        monkeypatch.setenv(key, value)
    secret = secrets.token_urlsafe(48)
    monkeypatch.setenv("LOCAL_JWT_SECRET", secret)
    # The full application discovers these production specs before lifespan.
    for module in ("date_time_tool", "analyze_tool", "refine_tool", "evaluate_tool"):
        importlib.import_module("src.domains.agentverse.tools." + module)
    registry_instances.agent_registry_instance.bind_spec(chat_spec)(ChatAgent)
    registry_instances.agent_registry_instance.bind_spec(form_agent_spec)(ChatAgent)
    registry_instances.role_registry_instance.register_role_spec(role_spec)
    registry_instances.role_registry_instance.register_role_spec(enki_role_spec)
    registry = SynapticRegistry()
    monkeypatch.setattr(registry_instances, "synaptic_registry_instance", registry)
    mongo = MongoDBClient(settings.database.resolved_uri, "g14_test_" + uuid4().hex)
    redis = Redis.from_url(url)
    repository = RedisRepositoryImpl(redis)
    container = Container()
    container.database.mongo_client.override(providers.Object(mongo))
    container.redis.redis_repository.override(providers.Object(repository))
    security = build_genesis_security(container)
    sessions = security["genesis_security"].sessions
    sessions._keys = ["g14:" + uuid4().hex + suffix for suffix in (":records", ":due")]
    container = initialize_domains(container, **security)
    # Declarative providers capture the production registry at class definition;
    # explicitly bind this test's isolated registry to both registration and dispatch.
    container.agentverse.synaptic_exchange.add_kwargs(registry=registry)
    app = FastAPI()
    app.container = container
    app.state.client_factory = ClientFactory({"llm": {"test": NoInference()}}, {"llm": "test"})
    app.include_router(agent_router)
    app.include_router(session_router)
    configure_genesis_transport(app, security["genesis_security"])

    def operator_token():
        return jwt.encode(
            {
                "tid": TENANT,
                "sub": OWNER,
                "sid": "g14-" + uuid4().hex,
                "exp": int(time.time() + 600),
                "iss": "CAOS-local",
                "aud": "CAOS-api",
            },
            secret,
            algorithm="HS256",
        )

    try:
        await app.state.start_genesis_sessions()
        token = operator_token()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://g14.test",
            headers={"Authorization": "Bearer " + token},
        ) as client:
            response = await client.post("/api/v2/agentverse/session/local")
            assert response.status_code == 201
            session_id = response.json()["session_id"]
            yield SimpleNamespace(
                app=app,
                client=client,
                registry=registry,
                mongo=mongo,
                av=container.agentverse,
                sessions=sessions,
                session_id=session_id,
                operator_token=operator_token,
            )
    finally:
        await container.agentverse.agent_lifecycle_service().shutdown()
        await sessions.close()
        await redis.delete(*sessions._keys)
        # All tenant revocation keys in this fixture live in disposable Redis.
        authority = container.agentverse.utu_authority()
        await redis.delete(authority._store()._key(TENANT))
        if mongo.client is not None:
            await mongo.client.drop_database(mongo.db_name)
        await mongo.disconnect()
        await redis.aclose()


def endpoint(signal=SIGNAL):
    return f"/api/v2/agentverse/agents/{JOSHUA}/signals/{signal}"


def payload():
    return {
        "creation": {
            "name": "Enki",
            "type": "form",
            "role_name": "enki",
            "personality_key": "empty_eva",
            "prompt": "You are Enki, keeper of CAOS agents.",
        }
    }


@pytest.mark.asyncio
async def test_local_startup_endpoint_and_verified_operator_session(local_app):
    s = local_app
    assert s.registry.resolve(SIGNAL, "1.0.0").provider == "system.genesis"
    agents = s.mongo.db["coll_agents"]
    # No Adam bootstrap; JOSHUA and Genesis are System Citizens: startup writes no Agent.
    assert await agents.count_documents({}) == 0
    # Existing signal interface requires activation of the initiating runtime only.
    assert (await s.client.post(endpoint(), json=payload())).status_code == 409
    assert (await s.client.post(f"/api/v2/agentverse/agents/{JOSHUA}/awaken")).status_code == 200
    validation = (
        await s.av.validation()
        .agent_validator()
        .validate(payload()["creation"], requester=User(id=OWNER, tenant_id=TENANT))
    )
    assert validation.valid, [i.model_dump() for i in validation.issues]
    result = await s.client.post(endpoint(), json=payload())
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["status"] == "provisioned" and body["agent_id"] == ENKI, body
    stored = await agents.find_one({"id": ENKI})
    assert (stored["type"], stored["role_name"], stored["runtime_version"]) == (
        "form",
        "enki",
        "v2",
    )
    assert body["correlation_id"] == body["operation_id"]
    repeat = await s.client.post(endpoint(), json=payload())
    assert repeat.json()["replayed"] is True
    assert repeat.json()["initiation_event_id"] == body["initiation_event_id"]
    historical = payload()
    historical["creation"].update(type="chat", role_name="general")
    assert (await s.client.post(endpoint(), json=historical)).status_code == 403
    for field, value in (("role_name", "general"), ("personality_key", "other")):
        incompatible = payload()
        incompatible["creation"][field] = value
        assert (await s.client.post(endpoint(), json=incompatible)).status_code == 403
    assert await agents.count_documents({"id": ENKI}) == 1
    # Both citizens resolved and executed with no coll_agents definition at any point.
    assert set(await agents.distinct("id")) == {ENKI}
    records = await s.av.caos_log_service().history(TENANT, body["correlation_id"])
    assert len(records) == 4
    assert [r.actor.principal_id for r in records] == [JOSHUA, GENESIS, GENESIS, GENESIS]
    assert {r.actor.user_id for r in records} == {OWNER}
    assert records[0].event_type == INITIATION_EVENT
    assert records[0].subject.resource_id == ENKI
    operation = await s.app.container.operations.managed_operation_repository_v2().inspect(
        UUID(body["operation_id"])
    )
    assert operation.snapshot.state.value == "succeeded"
    assert (
        s.av.instance_registry_v2().get(AgentInstanceKey(TENANT, OWNER, ENKI, s.session_id)) is None
    )
    assert (await s.client.get(f"/api/v2/agentverse/agents/{ENKI}")).json()["state"] == "sleeping"
    for signal in ("store.create", "store.create.provisioned", "data.validation"):
        assert (await s.client.post(endpoint(signal), json={})).status_code == 403
    async with s.sessions.authenticated(s.client.headers["Authorization"][7:]):
        principal = await s.av.utu_authority().resolve_agent_principal(
            AgentInstanceKey(TENANT, OWNER, ENKI, s.session_id)
        )
        grants = s.av.utu_authority().grants_for(principal).invoke
        assert grants == {"chat.context", "chat.response"}
        enki_client = s.av.synaptic_exchange().bind(principal)
        for signal in ("store.create", "store.create.provisioned", "data.validation", SIGNAL):
            with pytest.raises(EventPermissionError):
                await enki_client.request(signal, {})
    assert (await s.client.post(endpoint(), json={**payload(), "permit": {}})).status_code == 422
    assert (await s.client.post("/api/v2/agentverse/session/logout")).status_code == 204
    assert (await s.client.post(endpoint(), json=payload())).status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("new_session", [False, True])
async def test_real_endpoint_concurrency_and_interrupted_attempt_recovery(local_app, new_session):
    s = local_app
    assert (await s.client.post(f"/api/v2/agentverse/agents/{JOSHUA}/awaken")).status_code == 200
    adapter = s.registry.resolve(SIGNAL).handler.__self__
    provisioner = adapter._provisioner
    repository = provisioner._operations
    commit = repository.commit_outcome
    provisioner._lease_ms = 300

    async def lost_process(*args, **kwargs):
        raise RuntimeError("interrupted before outcome acknowledgement")

    repository.commit_outcome = lost_process
    with pytest.raises(RuntimeError):
        await s.client.post(endpoint(), json=payload())
    repository.commit_outcome = commit
    assert await s.mongo.db["coll_agents"].count_documents({"id": ENKI}) == 1
    before = (
        await s.mongo.db["coll_logs"].find({"event_type": {"$ne": INITIATION_EVENT}}).to_list(10)
    )
    if new_session:
        assert (await s.client.post("/api/v2/agentverse/session/logout")).status_code == 204
        s.client.headers["Authorization"] = "Bearer " + s.operator_token()
        assert (await s.client.post("/api/v2/agentverse/session/local")).status_code == 201
        assert (
            await s.client.post(f"/api/v2/agentverse/agents/{JOSHUA}/awaken")
        ).status_code == 200
    await asyncio.sleep(0.5)  # real Mongo server-time lease expiry
    responses = await asyncio.gather(*(s.client.post(endpoint(), json=payload()) for _ in range(4)))
    assert all(r.status_code == 200 for r in responses)
    assert {r.json()["status"] for r in responses} <= {"provisioned", "in_progress"}
    done = (await s.client.post(endpoint(), json=payload())).json()
    assert done["status"] == "provisioned"
    records = await s.av.caos_log_service().history(TENANT, done["correlation_id"])
    assert len(records) == (5 if new_session else 4)
    after = (
        await s.mongo.db["coll_logs"].find({"event_type": {"$ne": INITIATION_EVENT}}).to_list(10)
    )
    assert before == after  # original actor, permit evidence, time and content digest are retained
    operation = await repository.inspect(UUID(done["operation_id"]))
    assert len(operation.snapshot.attempts) == 2
    assert await s.mongo.db["coll_agents"].count_documents({"id": ENKI}) == 1


class EnkiModel:
    def __init__(self):
        self.calls = []
        self.client = SimpleNamespace(provider="test-provider", chat_deployment="test-model")

    async def get_response_async(self, **kwargs):
        self.calls.append(kwargs)
        return "I can prepare an Agent creation draft for your review."


@pytest.mark.asyncio
async def test_genesis_created_form_enki_converses_through_the_v2_runtime(local_app):
    from src.domains.agentverse.entities.conversation_v2 import ClientEventV2, ConversationV2

    s = local_app
    assert (await s.client.post(f"/api/v2/agentverse/agents/{JOSHUA}/awaken")).status_code == 200
    created = await s.client.post(endpoint(), json=payload())
    assert created.json()["status"] == "provisioned", created.text
    model = EnkiModel()
    s.app.state.client_factory = ClientFactory({"llm": {"test": model}}, {"llm": "test"})
    conversations = s.av.conversation_repository_v2()
    dispatcher = s.av.client_event_dispatcher_v2()
    token = s.client.headers["Authorization"][7:]
    # A deterministic citizen has no Cognitive Agent definition, so it cannot join
    # a conversation even though its session membership and approval are valid.
    for citizen in (JOSHUA, GENESIS):
        joined = await conversations.create(
            ConversationV2(tenant_id=TENANT, member_user_ids=(OWNER,), agent_ids=(citizen,))
        )
        async with s.sessions.authenticated(token) as session:
            with pytest.raises(EventPermissionError):
                await dispatcher.dispatch(
                    session,
                    ClientEventV2(
                        conversation_id=joined.id,
                        event_id="citizen-" + uuid4().hex,
                        type="chat.message",
                        response_mode="llm",
                        agent_ids=(citizen,),
                        message="Hello",
                    ),
                )
    assert model.calls == []
    conversation = await conversations.create(
        ConversationV2(tenant_id=TENANT, member_user_ids=(OWNER,), agent_ids=(ENKI,))
    )
    event = ClientEventV2(
        conversation_id=conversation.id,
        event_id="enki-" + uuid4().hex,
        type="chat.message",
        response_mode="llm",
        agent_ids=(ENKI,),
        message="Help me draft a weather agent.",
    )
    async with s.sessions.authenticated(token) as session:
        result = await dispatcher.dispatch(session, event)
    assert result["type"] == "chat.response", result
    assert len(model.calls) == 1
    instructions = model.calls[0]["instructions"]
    assert "Agent type: form" in instructions and "Enki" in instructions
    # Conversation activated Enki through Khepri; it holds only its approved grants.
    key = AgentInstanceKey(TENANT, OWNER, ENKI, s.session_id)
    assert s.av.instance_registry_v2().get(key) is not None
    async with s.sessions.authenticated(token):
        principal = await s.av.utu_authority().resolve_agent_principal(key)
        assert s.av.utu_authority().grants_for(principal).invoke == {
            "chat.context",
            "chat.response",
        }
    # Conversation creates no audit or Store evidence; Genesis evidence is unchanged.
    records = await s.av.caos_log_service().history(TENANT, created.json()["correlation_id"])
    assert len(records) == 4
    assert await s.mongo.db["coll_agents"].count_documents({}) == 1
