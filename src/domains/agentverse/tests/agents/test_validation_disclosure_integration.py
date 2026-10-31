"""VALIDATION-SEC-0.1 end to end (disposable Mongo and Redis, reviewed local policy).

The owner may awaken the Genesis System Citizen in their session and invoke its granted
``data.validation`` through the generic signal route, which returns the provider result
without the G3.3 envelope's scrubbing. Findings there must not name a Tool the human
cannot read. Requires ``OPS2_MONGO_INTEGRATION=1`` and ``UTU_TEST_REDIS_URL``.
"""

import json
from types import SimpleNamespace

import pytest

from src.base.entities.user import User
from src.domains.agentverse.dependencies.genesis_local import GENESIS, OWNER, TENANT
from src.domains.agentverse.registries.registries import registries
from src.domains.agentverse.services.registry_service import RegistryService
from src.domains.agentverse.tests.agents.test_genesis_initiation_integration import (
    local_app,  # noqa: F401
)
from src.domains.agentverse.tests.agents.test_recursive_genesis_integration import (
    AGENTS,
    signal,
    with_enki,
)

ROLE = "validation_sec_probe_role"


@pytest.mark.asyncio
async def test_generic_route_validation_never_names_a_hidden_required_tool(local_app):  # noqa: F811
    s = await with_enki(local_app)
    human = User(id=OWNER, tenant_id=TENANT)
    readable = set(RegistryService(registries).list_for_client("tool", human))
    tool_keys = registries["tool"].keys()  # Registry API, not a dict.
    hidden = sorted(k for k in tool_keys if k not in readable)
    assert hidden, "the real registry has internal Tools"
    roles = registries["role"]
    roles.register(
        name=ROLE,
        key=ROLE,
        component=SimpleNamespace(handlers={}, policy={}),
        metadata={
            "ownership": "global",
            "agent_group": "interactive_ui",
            "agent_subclass": "conversational",
            "capabilities": {"required_tools": {hidden[0]: "Hidden"}},
        },
    )
    try:
        assert ROLE in RegistryService(registries).list_for_client("role", human)
        assert (await s.client.post(f"{AGENTS}/{GENESIS}/awaken")).status_code == 200
        payload = {
            "resource_type": "agent",
            "payload": {
                "name": "Probe",
                "type": "chat",
                "role_name": ROLE,
                "personality_key": "empty_eva",
                "prompt": "Probe.",
            },
        }
        response = await signal(s, GENESIS, "data.validation", payload)
        assert response.status_code == 200, response.text
        result = response.json()["result"]
        assert result["valid"] is False
        assert [(i["path"], i["code"]) for i in result["issues"]] == [
            ("tools", "composition.required_tool_missing")
        ]
        assert result["issues"][0]["message"] == (
            "Role requires a tool that is unavailable to this requester"
        )
        assert hidden[0] not in json.dumps(response.json())
    finally:
        roles.unregister(ROLE)
