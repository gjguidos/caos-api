"""Gateway on real disposable Mongo/Redis; inherited setup alone provisions Enki."""

import copy

import httpx
import pytest

from src.domains.agentverse.dependencies.genesis_local import ENKI
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey
from src.domains.agentverse.tests.agents import test_genesis_initiation_integration as genesis_tests
from src.domains.agentverse.tests.agents.test_recursive_genesis_integration import with_enki

PATH = f"/api/v2/agentverse/agents/{ENKI}/creation-draft/validate"
local_app = genesis_tests.local_app


def payload():
    return {
        "contract": "caos.agent_creation_draft",
        "schema_version": 1,
        "resource_type": "agent",
        "creation": {
            "name": "Preview Only",
            "type": "chat",
            "role_name": "general",
            "personality_key": "empty_eva",
            "prompt": "A definition for read-only validation.",
        },
    }


@pytest.mark.asyncio
async def test_authorized_sleeping_source_real_gateway_has_no_creation_effects(local_app):
    s = await with_enki(local_app)
    before_agents = await s.mongo.db["coll_agents"].find({}).to_list(length=None)
    before_logs = await s.mongo.db["coll_logs"].count_documents({})
    before_policy = copy.deepcopy(s.av.synaptic_authorization()._grants)
    before_approvals = dict(s.av.genesis_approvals())
    before_instances = dict(s.av.instance_registry_v2()._map)
    key = AgentInstanceKey("caos-local", "00000000-0000-4000-8000-000000000001", ENKI, s.session_id)
    assert s.av.instance_registry_v2().get(key) is None
    response = await s.client.post(PATH, json=payload())
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["validation"]["valid"] and result["composition"]["compatible"]
    assert result["capability"]["version"] == "1.0.0"
    assert result["composition"]["runtime_execution_authorized"] is False
    assert s.av.instance_registry_v2().get(key) is None
    assert await s.mongo.db["coll_agents"].find({}).to_list(length=None) == before_agents
    assert await s.mongo.db["coll_logs"].count_documents({}) == before_logs
    assert s.av.synaptic_authorization()._grants == before_policy
    assert dict(s.av.genesis_approvals()) == before_approvals
    assert dict(s.av.instance_registry_v2()._map) == before_instances
    assert "approval_id" not in result and "review" not in result


@pytest.mark.asyncio
async def test_gateway_http_denials_and_exact_provider_removal(local_app):
    s = await with_enki(local_app)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=s.app), base_url="http://test"
    ) as anonymous:
        assert (await anonymous.post(PATH, json=payload())).status_code == 403
    for field in ("executor_id", "tenant_id", "requester", "roles", "version", "signal"):
        assert (await s.client.post(PATH, json={**payload(), field: "forged"})).status_code == 422
    assert (
        await s.client.post(PATH.replace(ENKI, "unapproved-source"), json=payload())
    ).status_code == 403
    forged = payload()
    forged["creation"]["roles"] = ["agentverse-admin"]
    result = (await s.client.post(PATH, json=forged)).json()
    assert not result["validation"]["valid"]
    s.registry.unregister("data.validation")
    assert (await s.client.post(PATH, json=payload())).status_code == 503
