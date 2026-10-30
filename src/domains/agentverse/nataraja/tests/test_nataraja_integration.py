"""Nataraja against a disposable environment (real Mongo ``caos_test_*`` and Redis).

Inspection must leave every collection and Redis key byte-identical, classify a
deferred Thoth correctly, and observe the governed Enki → Genesis → Thoth result.
"""

import json
import os

import httpx
import pytest
from redis.asyncio import Redis

from src.base.operations.v2.admission import sha256_hex
from src.domains.agentverse.dependencies.genesis_local import ENKI, OWNER, TENANT, THOTH
from src.domains.agentverse.interfaces.api.v2 import nataraja_runtime
from src.domains.agentverse.nataraja.manifest import load_manifest
from src.domains.agentverse.nataraja.observe import AsyncReadOnlyRedis, MotorReadOnlyMongo, observe
from src.domains.agentverse.nataraja.plan import evaluate
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey
from src.domains.agentverse.tests.agents import test_genesis_initiation_integration as genesis_tests
from src.domains.agentverse.tests.agents.test_recursive_genesis_integration import (
    AGENTS,
    THOTH_SIGNAL,
    signal,
    thoth_payload,
    with_enki,
)

local_app = genesis_tests.local_app


async def fingerprint(s, redis) -> str:
    collections = {}
    for name in sorted(await s.mongo.db.list_collection_names()):
        rows = await s.mongo.db[name].find({}).to_list(length=None)
        collections[name] = sorted(json.dumps(row, sort_keys=True, default=str) for row in rows)
    keys = {}
    async for key in redis.scan_iter(count=500):
        kind = (await redis.type(key)).decode()
        if kind == "hash":
            value = {k.decode(): v.decode() for k, v in (await redis.hgetall(key)).items()}
        elif kind == "string":
            value = (await redis.get(key)).decode()
        else:
            value = kind
        keys[key.decode()] = json.dumps(value, sort_keys=True)
    return sha256_hex({"mongo": collections, "redis": sorted(keys.items())})


async def runtime_of(app, client=("127.0.0.1", 50000)):
    transport = httpx.ASGITransport(app=app, client=client)
    async with httpx.AsyncClient(transport=transport, base_url="http://runtime") as http:
        return await http.get("/internal/dev/nataraja/runtime")


async def inspected(s, redis):
    async def runtime():
        return (await runtime_of(s.app)).json()

    observation = await observe(
        runtime=runtime,
        tenant_id="caos-local",
        mongo=MotorReadOnlyMongo(s.mongo.db),
        redis=AsyncReadOnlyRedis(redis),
        container=s.app.container,
        targets={},
    )
    return evaluate(load_manifest(), observation, mode="reconcile")


@pytest.mark.asyncio
async def test_inspection_is_read_only_and_tracks_the_genesis_lifecycle(local_app):
    s = await with_enki(local_app)
    s.app.include_router(nataraja_runtime.router)
    # The runtime route exists only for loopback callers in local development.
    assert (await runtime_of(s.app, ("10.0.0.5", 1))).status_code == 404
    redis = Redis.from_url(os.environ["UTU_TEST_REDIS_URL"])
    try:
        before = await fingerprint(s, redis)
        report = await inspected(s, redis)
        assert await fingerprint(s, redis) == before, "inspection changed the environment"
        resources = {r["id"]: r for r in report["resources"]}
        # The fixture creates Enki through JOSHUA initiation (session origin), not the
        # startup bootstrap lease: reported as a warning, never as a missing Agent.
        assert resources[ENKI]["present"] is True and resources[ENKI]["status"] == "warning"
        assert {f["code"] for f in report["findings"] if f["resource"] == ENKI} == {
            "agent.creation_origin",
            "agent.runtime_presence",
        }
        assert resources[THOTH]["status"] == "deferred"
        assert {
            r["name"]: r["status"] for r in report["resources"] if r["kind"] == "system_citizen"
        } == {
            "JOSHUA": "passed",
            "Genesis": "passed",
        }
        assert resources["genesis-policy"]["status"] == "passed"
        # Runtime-confirmed providers and the separated Thoth lifecycle.
        providers = [r for r in report["resources"] if r["kind"] == "provider"]
        assert providers and {r["runtime"] for r in providers} == {"confirmed"}
        assert resources["api-process"]["observation"] == "confirmed"
        assert "runtime.activation_coupled" not in {f["code"] for f in report["findings"]}

        # The governed path (not Nataraja) creates Thoth; inspection then observes it.
        assert (await s.client.post(f"{AGENTS}/{ENKI}/awaken")).status_code == 200
        created = await signal(s, ENKI, THOTH_SIGNAL, thoth_payload())
        assert created.status_code == 200, created.text
        after = await inspected(s, redis)
        resources = {r["id"]: r for r in after["resources"]}
        assert resources[THOTH]["present"] is True
        assert "agent.store_evidence_missing" not in {
            f["code"] for f in after["findings"] if f["resource"] == THOTH
        }
        # Created sleeping: only Thoth's own, separate Khepri activation remains possible.
        thoth_key = AgentInstanceKey(TENANT, OWNER, THOTH, s.session_id)
        assert s.av.instance_registry_v2().get(thoth_key) is None
        assert "activation" not in created.json()
        assert not [p for p in after["reconciliation_plan"] if p["resource"] == THOTH]
        assert s.model.calls == []
    finally:
        await redis.aclose()
