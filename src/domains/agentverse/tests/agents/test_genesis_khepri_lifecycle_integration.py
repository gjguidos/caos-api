"""DEV-0.2: Genesis owns creation, Khepri owns activation (disposable Mongo/Redis).

Startup bootstrap → sleeping Enki → denied while sleeping → explicit Khepri activation
→ governed Thoth creation, persisted sleeping and idempotent → separate Khepri
activation, each with its own correctly correlated Seshat evidence. No unexpected
grants, approvals, Agents or model calls.
"""

import copy

import pytest

from src.base.infrastructure.ai.client_factory import ClientFactory
from src.domains.agentverse.agents.genesis.activation import ACTIVATION_EVENT, READINESS_EVENT
from src.domains.agentverse.agents.genesis.initiation import INITIATION_EVENT, THOTH_SIGNAL
from src.domains.agentverse.dependencies.genesis_local import ENKI, GENESIS, OWNER, TENANT, THOTH
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey
from src.domains.agentverse.tests.agents import test_genesis_initiation_integration as genesis_tests
from src.domains.agentverse.tests.agents.test_recursive_genesis_integration import (
    AGENTS,
    ThothModel,
    activate,
    signal,
    thoth_payload,
)
from src.domains.agentverse.tools.chat_tool_v2.synaptic import chat_context

local_app = genesis_tests.local_app
GENESIS_EVENTS = (
    "genesis.validation.completed",
    "genesis.composition.completed",
    "genesis.store.completed",
)


@pytest.mark.asyncio
async def test_creation_and_activation_are_separate_lifecycle_operations(local_app):
    s = local_app
    s.registry.subscribe("chat.context", provider="tool.chat")(chat_context)
    s.model = ThothModel()
    s.app.state.client_factory = ClientFactory({"llm": {"test": s.model}}, {"llm": "test"})
    agents, logs = s.mongo.db["coll_agents"], s.mongo.db["coll_logs"]
    grants = copy.deepcopy(s.av.synaptic_authorization()._grants)
    approvals = dict(s.av.genesis_approvals())
    instances = s.av.instance_registry_v2()
    enki_key = AgentInstanceKey(TENANT, OWNER, ENKI, s.session_id)
    thoth_key = AgentInstanceKey(TENANT, OWNER, THOTH, s.session_id)

    # Enki is bootstrapped exactly once and starts sleeping; a restart only verifies.
    assert await s.app.state.bootstrap_genesis() == "created"
    evidence = await logs.count_documents({})
    assert await s.app.state.bootstrap_genesis() == "present"
    assert (await agents.count_documents({}), await logs.count_documents({})) == (1, evidence)
    assert instances.get(enki_key) is None

    # Creation of Thoth is denied while Enki sleeps; Khepri activates Enki on request.
    assert (await signal(s, ENKI, THOTH_SIGNAL, thoth_payload())).status_code == 409
    assert await agents.count_documents({"id": THOTH}) == 0
    assert (await s.client.post(f"{AGENTS}/{ENKI}/awaken")).status_code == 200
    assert instances.get(enki_key) is not None

    # Genesis persists Thoth exactly once, sleeping; creation never activates.
    body = (await signal(s, ENKI, THOTH_SIGNAL, thoth_payload())).json()
    assert (body["status"], body["agent_id"], body["created"]) == ("provisioned", THOTH, True)
    assert "activation" not in body
    assert instances.get(thoth_key) is None
    creation = await s.av.caos_log_service().history(TENANT, body["correlation_id"])
    assert sorted(r.event_type for r in creation) == sorted((INITIATION_EVENT, *GENESIS_EVENTS))
    again = (await signal(s, ENKI, THOTH_SIGNAL, thoth_payload())).json()
    assert (again["replayed"], again["operation_id"]) == (True, body["operation_id"])
    assert await agents.count_documents({"id": THOTH}) == 1
    assert instances.get(thoth_key) is None
    replayed = await s.av.caos_log_service().history(TENANT, body["correlation_id"])
    # Same initiating authority: the replay reuses its evidence and adds nothing.
    assert [r.event_id for r in replayed] == [r.event_id for r in creation]

    # Thoth activation is a separate Khepri operation with its own authority.
    activated = await activate(s, THOTH, body["operation_id"])
    assert activated.status_code == 200, activated.text
    assert (activated.json()["state"], activated.json()["ready"]) == ("active", True)
    assert instances.get(thoth_key) is not None
    records = await s.av.caos_log_service().history(TENANT, body["correlation_id"])
    by_type = {r.event_type: r for r in records}
    store = by_type["genesis.store.completed"]
    activation, readiness = by_type[ACTIVATION_EVENT], by_type[READINESS_EVENT]
    assert store.actor.principal_id == GENESIS and store.subject.resource_id == THOTH
    assert (activation.actor.principal_id, readiness.actor.principal_id) == (THOTH, THOTH)
    assert activation.causation_id == store.event_id
    assert readiness.causation_id == activation.event_id
    initiation = next(r for r in records if r.event_type == INITIATION_EVENT)
    # Independent authorities: Enki initiated, Genesis created, Thoth's permit activated.
    assert (
        len(
            {
                initiation.authorization.evidence_digest,
                store.authorization.evidence_digest,
                activation.authorization.evidence_digest,
            }
        )
        == 3
    )
    assert {r.actor.user_id for r in records} == {OWNER}

    # No unexpected grants, approvals, Agents or model inference.
    assert s.av.synaptic_authorization()._grants == grants
    assert dict(s.av.genesis_approvals()) == approvals
    assert set(await agents.distinct("id")) == {ENKI, THOTH}
    assert s.model.calls == []


@pytest.mark.asyncio
async def test_concurrent_activation_yields_one_runtime_and_requires_current_utu(local_app):
    import asyncio
    import os

    from redis.asyncio import Redis

    from src.domains.agentverse.runtime.presence import AgentPresenceStore
    from src.domains.agentverse.tests.agents.test_recursive_genesis_integration import with_enki

    s = await with_enki(local_app)
    key = AgentInstanceKey(TENANT, OWNER, ENKI, s.session_id)
    responses = await asyncio.gather(*[s.client.post(f"{AGENTS}/{ENKI}/awaken") for _ in range(6)])
    assert {r.status_code for r in responses} == {200}, [r.text for r in responses]
    assert {r.json()["state"] for r in responses} == {"active"}
    # One managed runtime and one Redis ownership lease for the instance key.
    assert s.av.instance_registry_v2().get(key) is not None
    redis = Redis.from_url(os.environ["UTU_TEST_REDIS_URL"])
    try:
        assert await redis.exists(AgentPresenceStore._key(key)) == 1
    finally:
        await redis.aclose()

    # Activation needs current UTU authority: a revoked scope cannot be awakened anew.
    assert (await s.client.post(f"{AGENTS}/{ENKI}/sleep")).status_code == 200
    await s.av.utu_authority().revoke(TENANT, agent_id=ENKI)
    assert (await s.client.post(f"{AGENTS}/{ENKI}/awaken")).status_code == 403
    assert s.av.instance_registry_v2().get(key) is None
