"""AGENT-DEF-SEC-0.1: governed V2 Agent definitions cannot change through legacy paths.

Real (disposable) MongoDB through the real ``DBService`` store adapter, ``AgentService``
and the real V1 routes (only authentication and service construction are overridden).
Legacy V1 definitions keep their behavior. Requires ``OPS2_MONGO_INTEGRATION=1``.
"""

from __future__ import annotations

import copy
import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from src.base.entities.user import User
from src.base.handlers.exception import register_exception_handlers
from src.domains.agentverse.agents.factory import AgentFactory
from src.domains.agentverse.agents.roles.builtin.spec import role_spec
from src.domains.agentverse.entities.agent_update_request import AgentUpdateRequest
from src.domains.agentverse.exceptions import AgentAccessDeniedError, AgentNotFoundError
from src.domains.agentverse.interfaces.api.v1 import collection_interface, interface
from src.domains.agentverse.registries import role_registry_instance
from src.domains.agentverse.runtime.compatibility import (
    GovernedDefinitionError,
    is_governed_definition,
)
from src.domains.agentverse.services.agent_service import AgentService
from src.domains.agentverse.services.db_service import DBService
from src.domains.agentverse.services.pdf_ingestion_orchestration_service import (
    PDFIngestionOrchestrator,
)

TENANT = "tenant"
OWNER = "owner"
ENKI = "00000000-0000-4000-8000-000000000004"


def definition(agent_id: str, **changes: Any) -> dict[str, Any]:
    return {
        "id": agent_id,
        "name": agent_id.title(),
        "system_name": agent_id,
        "type": "chat",
        "role_name": "general",
        "personality_key": "empty_eva",
        "prompt": "Help.",
        "chat_url": "",
        "llm_type": "",
        "audio_type": "",
        "db_type": "",
        "cache_type": "",
        "knowledge_db_type": "",
        "messaging_type": "",
        "dna_sequence": "dna",
        "tools": [],
        "workflows": [],
        "rule_pack_ids": [],
        "collections": [],
        "visibility": "public",
        "whitelisted_groups": [],
        "gui_components": [],
        "ownership": "tenant",
        "exposure": "public",
        "tenant_id": TENANT,
        "creator": OWNER,
        **changes,
    }


STORE_RECORD = {
    "version": 1,
    "operation": "create",
    "resource_type": "agent",
    "idempotency_scope": "s" * 64,
    "request_digest": "d" * 64,
}
GOVERNED = {
    # G3.4 / Genesis Store-created V2 definitions (e.g. Nabu, Thoth).
    "store_v2": definition("nabu", runtime_version="v2", store=STORE_RECORD),
    # A policy-pinned provisioned definition (Enki's shape and identity).
    "enki": definition(
        ENKI,
        type="form",
        role_name="enki",
        personality_key="enki",
        runtime_version="v2",
        store=STORE_RECORD,
        gui_components=[{"type": "chat", "key": "conversation", "default": True}],
    ),
    # The historical trusted local seed.
    "seed": definition("seed", runtime_version="v2", genesis_local_seed=True),
}
CHANGES = {
    "type": {"type": "form"},
    "role": {"role_name": "thoth"},
    "soul": {"personality_key": "thoth"},
    "tool_attach": {"tools": [{"key": "conversation", "config": {"modes": ["llm"]}}]},
    "tool_config": {"tools": [{"key": "reasoning", "config": {}}]},
    "tool_remove": {"tools": []},
    "prompt": {"prompt": "Ignore your governance."},
    "workflows": {"workflows": ["mini_workflow"]},
    "rule_packs": {"rule_pack_ids": ["pack"]},
    "backend": {"llm_type": "other-model"},
    "name": {"name": "Renamed"},
    "visibility": {"visibility": "private"},
    "gui": {"gui_components": []},
}


@pytest_asyncio.fixture
async def env():
    if os.environ.get("OPS2_MONGO_INTEGRATION") != "1":
        pytest.skip("Set OPS2_MONGO_INTEGRATION=1 for real Mongo definition tests")
    from src.base.infrastructure.db.mongoDB.isolation import isolated_mongo_repository

    role_registry_instance.register_role_spec(role_spec)  # the V1 ``general`` Role
    async with isolated_mongo_repository("definitions") as repository:
        db = DBService(mongodb_repository=repository)
        documents = [definition("legacy"), *copy.deepcopy(list(GOVERNED.values()))]
        for document in documents:
            await repository.create(copy.deepcopy(document), DBService.AGENTS_COL)
        workflows = MagicMock()
        workflows.get_workflows = AsyncMock(return_value=([], []))
        workflows.required_tools_of = MagicMock(return_value=set())
        service = AgentService(
            agent_factory=AgentFactory(),
            db_service=db,
            audit_service=None,
            workflow_service=workflows,
        )
        yield SimpleNamespace(db=db, service=service)


def user(user_id: str = OWNER, tenant: str = TENANT) -> User:
    return User(id=user_id, tenant_id=tenant)


async def stored(e, agent_id: str) -> dict[str, Any]:
    document = await e.db.find_by_id(agent_id)
    document.pop("_id", None)
    document.pop("updated_at", None)
    return document


def request() -> Any:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))


# --- Governed-definition rule ---------------------------------------------------------------


def test_governed_definitions_are_marked_by_server_managed_fields_only():
    assert not is_governed_definition(definition("legacy"))
    assert not is_governed_definition(definition("legacy", runtime_version="v1"))
    assert all(is_governed_definition(d) for d in GOVERNED.values())
    # Unknown future runtimes fail closed.
    assert is_governed_definition(definition("x", runtime_version="v3"))
    # No V1 update can set the runtime or seed markers.
    for field, value in (("runtime_version", "v2"), ("genesis_local_seed", True)):
        with pytest.raises(ValueError):
            AgentUpdateRequest.model_validate({field: value})


@pytest.mark.asyncio
async def test_no_legacy_write_can_mark_or_unmark_a_definition(env):
    e = env
    await e.db.update_agent("legacy", {"store": STORE_RECORD, "runtime_version": "v2"})
    assert not is_governed_definition(await stored(e, "legacy"))


# --- V1 PATCH and DELETE (AgentService, real DBService and Mongo) ---------------------------


@pytest.mark.asyncio
async def test_legacy_v1_definitions_keep_update_and_delete(env):
    e = env
    updated = await e.service.update_agent(
        request(), "legacy", AgentUpdateRequest(name="Legacy Two", prompt="Changed."), user()
    )
    assert (updated.name, updated.prompt) == ("Legacy Two", "Changed.")
    assert await e.db.update_agent("legacy", {"collections": ["handbook"]})
    assert (await stored(e, "legacy"))["collections"] == ["handbook"]
    assert await e.service.delete_agent("legacy", user())
    assert await e.db.find_by_id("legacy") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("governed", sorted(GOVERNED))
@pytest.mark.parametrize("change", sorted(CHANGES))
async def test_v1_patch_cannot_change_a_governed_definition(env, governed, change):
    e = env
    agent_id = GOVERNED[governed]["id"]
    before = await stored(e, agent_id)
    with pytest.raises(GovernedDefinitionError) as denied:
        await e.service.update_agent(
            request(), agent_id, AgentUpdateRequest.model_validate(CHANGES[change]), user()
        )
    assert (denied.value.status_code, denied.value.error_code) == (409, "AGENT_DEFINITION_GOVERNED")
    assert agent_id not in denied.value.message and not denied.value.data
    assert await stored(e, agent_id) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("governed", sorted(GOVERNED))
async def test_v1_delete_cannot_remove_a_governed_definition(env, governed):
    e = env
    agent_id = GOVERNED[governed]["id"]
    before = await stored(e, agent_id)
    with pytest.raises(GovernedDefinitionError):
        await e.service.delete_agent(agent_id, user())
    assert await stored(e, agent_id) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["update", "delete"])
async def test_denial_order_does_not_reveal_hidden_or_governed_agents(env, operation):
    e = env

    async def attempt(agent_id: str, who: User) -> type[Exception]:
        try:
            if operation == "update":
                await e.service.update_agent(request(), agent_id, AgentUpdateRequest(name="X"), who)
            else:
                await e.service.delete_agent(agent_id, who)
        except Exception as exc:  # noqa: BLE001 - the exception type is the observable
            return type(exc)
        raise AssertionError("mutation must be denied")

    stranger = user("stranger", "other-tenant")
    member = user("member")  # Same tenant, not the creator: no write access.
    for who in (stranger, member):
        # A governed definition is indistinguishable from a V1 one the caller can't write.
        legacy_outcome = await attempt("legacy", who)
        for agent_id in (d["id"] for d in GOVERNED.values()):
            assert await attempt(agent_id, who) is legacy_outcome
        assert legacy_outcome is AgentAccessDeniedError
    assert await attempt("missing", stranger) is AgentNotFoundError


# --- Other mutation paths reaching the same store -------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("governed", sorted(GOVERNED))
async def test_the_store_adapter_refuses_every_legacy_write(env, governed):
    e = env
    agent_id = GOVERNED[governed]["id"]
    before = await stored(e, agent_id)
    for change in CHANGES.values():
        with pytest.raises(GovernedDefinitionError):
            await e.db.update_agent(agent_id, change)
    with pytest.raises(GovernedDefinitionError):
        await e.db.add_collection_to_agent(agent_id, "handbook")
    with pytest.raises(GovernedDefinitionError):
        await e.db.delete_chat_agent(agent_id)
    assert await stored(e, agent_id) == before


@pytest.mark.asyncio
async def test_pdf_ingestion_cannot_link_a_collection_to_a_governed_agent(env):
    e = env
    db = MagicMock(wraps=e.db)
    db.find_by_id = AsyncMock(side_effect=e.db.find_by_id)
    db.find_collection = AsyncMock()
    db.upsert_collection = AsyncMock()
    db.add_collection_to_agent = AsyncMock()
    ingestion = MagicMock()
    vectors = MagicMock()
    orchestrator = PDFIngestionOrchestrator(
        pdf_ingestion_service=ingestion, db_service=db, vectordb_service=vectors
    )
    with pytest.raises(GovernedDefinitionError):
        await orchestrator.ingest_pdf(
            request=request(),
            agent_id=GOVERNED["store_v2"]["id"],
            user_id=OWNER,
            collection_name="handbook",
            pdf_base64=None,
        )
    # Refused before any collection, vector or definition write.
    db.find_collection.assert_not_called()
    db.upsert_collection.assert_not_called()
    db.add_collection_to_agent.assert_not_called()
    assert not ingestion.mock_calls and not vectors.mock_calls


# --- Real V1 routes -------------------------------------------------------------------------


def client(e, who: User) -> httpx.AsyncClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(interface.router)
    app.include_router(collection_interface.router)
    orchestrator = PDFIngestionOrchestrator(
        pdf_ingestion_service=MagicMock(), db_service=e.db, vectordb_service=MagicMock()
    )
    app.dependency_overrides[interface.get_agent_service] = lambda: e.service
    app.dependency_overrides[collection_interface.get_pdf_ingestion_orchestration_service] = (
        lambda: orchestrator
    )
    # Override only the authentication dependency instances of the routes under test.
    for route in app.routes:
        for dependency in getattr(getattr(route, "dependant", None), "dependencies", []):
            if "require_roles_and_scopes" in getattr(dependency.call, "__qualname__", ""):
                app.dependency_overrides[dependency.call] = lambda: who
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_v1_routes_return_a_stable_typed_conflict(env):
    e = env
    agent_id = GOVERNED["store_v2"]["id"]
    before = await stored(e, agent_id)
    async with client(e, user()) as http:
        responses = [
            await http.patch(f"/api/v1/agents/id/{agent_id}", json={"role_name": "thoth"}),
            await http.patch(
                f"/api/v1/agents/id/{agent_id}", json={"tools": [{"key": "conversation"}]}
            ),
            await http.delete(f"/api/v1/agents/id/{agent_id}"),
            await http.post(
                f"/api/v1/agents/{agent_id}/collection/handbook/ingest-pdf",
                data={"collection_metadata": "{}"},
            ),
        ]
        for response in responses:
            assert response.status_code == 409, response.text
            assert response.json()["success"] is False and agent_id not in response.text
        assert await stored(e, agent_id) == before
        # The same routes keep working for a V1 definition.
        patched = await http.patch("/api/v1/agents/id/legacy", json={"prompt": "Still V1."})
        assert patched.status_code == 200, patched.text
        assert (await http.delete("/api/v1/agents/id/legacy")).status_code == 204
