"""RG-02D: ownership is explicit and independent from exposure/creator identity."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.base.entities.user import User
from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.agents.lifecycle.tests.test_utu import setup
from src.domains.agentverse.entities.agent_update_request import AgentUpdateRequest
from src.domains.agentverse.entities.conversation_v2 import ConversationV2
from src.domains.agentverse.entities.resource_ownership import inventory_ownership
from src.domains.agentverse.entities.workflow import WorkflowCreateRequest, WorkflowUpdateRequest
from src.domains.agentverse.exceptions import (
    AgentAccessDeniedError,
    WorkflowNotFoundError,
    WorkflowValidationError,
)
from src.domains.agentverse.registries.base import Registry
from src.domains.agentverse.security.utu import UTUAuthority
from src.domains.agentverse.services.agent_service import AgentService
from src.domains.agentverse.services.registry_service import RegistryService
from src.domains.agentverse.services.workflow_service import WorkflowService
from src.domains.agentverse.tests.services.test_creation_governance import (
    assert_no_work,
    config,
    service,
)
from src.domains.agentverse.tests.services.test_runtime_compatibility import definition

USER = User(id="owner", tenant_id="tenant", roles=["agentverse-admin"])


@pytest.mark.parametrize("operation", ["DISCOVER", "READ"])
@pytest.mark.parametrize(
    "metadata,allowed",
    [
        ({}, False),
        ({"tenant_id": "tenant"}, False),
        ({"creator": "owner", "visibility": "public"}, False),
        ({"ownership": "system", "exposure": "public"}, False),
        ({"ownership": "global"}, True),
        ({"ownership": "global", "exposure": "internal"}, False),
        ({"ownership": "global", "tenant_id": "tenant"}, False),
        ({"ownership": "global", "tenant_scoped": True}, False),
        ({"ownership": "tenant"}, False),
        ({"ownership": "tenant", "tenant_id": ""}, False),
        ({"ownership": "tenant", "tenant_id": " tenant "}, False),
        ({"ownership": "tenant", "tenant_id": "other"}, False),
        ({"ownership": "tenant", "tenant_id": "tenant"}, True),
        ({"ownership": "shared"}, False),
        ({"ownership": {"type": "global"}}, False),
    ],
)
def test_policy_matrix(operation, metadata, allowed):
    assert UTUAuthority.can_access_resource(USER, metadata, operation=operation) is allowed


@pytest.mark.parametrize("kind", sorted(RegistryService.CLIENT_KINDS))
def test_all_catalog_kinds_require_explicit_ownership_and_preserve_raw_runtime(kind):
    reg = Registry(kind)
    for key, metadata in {
        "legacy": {},
        "old_tenant": {"tenant_id": "tenant"},
        "system": {"ownership": "system"},
        "shared": {"ownership": "global"},
        "mine": {"ownership": "tenant", "tenant_id": "tenant"},
    }.items():
        reg.register(name=key, component=object, metadata=metadata)
    svc = RegistryService({kind: reg})
    assert svc.list_for_client(kind, USER) == ["shared", "mine"]
    for key in ("legacy", "old_tenant", "system"):
        with pytest.raises(KeyError):
            svc.get_info_for_client(kind, key, USER)
        assert svc.get_component(kind, key) is object
    assert svc.list_for_client(kind, User(id="owner")) == ["shared"]


def test_nested_references_do_not_inherit_parent_ownership_but_plain_config_does():
    reg = Registry("tool")
    reg.register(name="unknown", component=object)
    svc = RegistryService({"tool": reg})
    parent = {"ownership": "tenant", "tenant_id": "tenant"}
    assert svc.client_metadata_allowed({**parent, "config": {"prompt": "ok"}}, USER)
    assert not svc.client_metadata_allowed({**parent, "tools": ["unknown"]}, USER)
    assert not svc.client_metadata_allowed({**parent, "expanded": {"tenant_id": "other"}}, USER)
    assert not svc.client_metadata_allowed({**parent, "expanded": {"ownership": "system"}}, USER)


@pytest.mark.asyncio
@pytest.mark.parametrize("tenant", [None, "", " ", " tenant "])
async def test_creation_requires_verified_nonempty_tenant_before_work(tenant):
    user = User(id="owner", tenant_id=tenant, roles=["agentverse-admin"])
    agent = service()
    with pytest.raises(AgentAccessDeniedError):
        await agent.create_agent(Mock(), SimpleNamespace(), config(tenant_id="spoof"), user)
    assert_no_work(agent)
    db = SimpleNamespace(find_one=AsyncMock(), repository=SimpleNamespace(create=AsyncMock()))
    with pytest.raises(WorkflowValidationError, match="verified tenant"):
        await WorkflowService(db).create_workflow(
            WorkflowCreateRequest(name="x", definition={}), user
        )
    db.find_one.assert_not_awaited()
    db.repository.create.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("ownership", ["system", "global", "tenant"])
async def test_client_creation_cannot_select_ownership(ownership):
    svc = service()
    svc.agent_factory.create_agent.return_value = definition()
    svc.db_service.store_agent.side_effect = lambda _, agent: agent.model_dump()
    result = await svc.create_agent(
        Mock(), SimpleNamespace(), config(ownership=ownership, tenant_id="other"), USER
    )
    assert result.ownership == "tenant" and result.tenant_id == "tenant"
    passed = svc.agent_factory.create_agent.call_args.args[1]
    assert "ownership" not in passed.model_dump() and "tenant_id" not in passed.model_dump()
    db = SimpleNamespace(
        find_one=AsyncMock(return_value=None), repository=SimpleNamespace(create=AsyncMock())
    )
    workflow = WorkflowService(db)
    workflow._assert_valid = Mock(return_value=[])
    result = await workflow.create_workflow(
        WorkflowCreateRequest.model_validate(
            {"name": "flow", "definition": {}, "ownership": ownership, "tenant_id": "other"}
        ),
        USER,
    )
    assert result.ownership == "tenant" and result.tenant_id == "tenant"


@pytest.mark.parametrize("field", ["ownership", "tenant_id", "tenant_scoped"])
def test_agent_patch_cannot_reclassify(field):
    with pytest.raises(ValueError):
        AgentUpdateRequest.model_validate({field: "global"})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {"tenant_id": "tenant"},
        {"ownership": "system"},
        {"ownership": "global", "tenant_scoped": True},
        {"ownership": "invalid"},
    ],
)
async def test_raw_legacy_agents_denied_before_model_or_runtime(metadata):
    row = {**definition().model_dump(exclude={"ownership", "tenant_id"}), **metadata}
    db = SimpleNamespace(
        find_by_id=AsyncMock(return_value=row), find_all=AsyncMock(return_value=[row])
    )
    factory = Mock()
    svc = AgentService(factory, db, None, None)
    assert await svc.list_agents(USER) == []
    with pytest.raises(AgentAccessDeniedError):
        await svc.get_agent(row["id"], USER)
    factory.build_agent.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("ownership", [None, "system", "global", "tenant"])
async def test_v2_conversation_participants_require_owned_scope(ownership):
    env = setup()
    row = {"id": "agent", "creator": "owner", "ownership": ownership}
    if ownership == "tenant":
        row["tenant_id"] = "tenant"
    env.definitions.find_by_id.return_value = row
    conversation = ConversationV2(
        tenant_id="tenant", member_user_ids=("owner",), agent_ids=("agent",)
    )
    if ownership in ("global", "tenant"):
        await env.utu.authorize_conversation(session_id="session", conversation=conversation)
    else:
        with pytest.raises(EventPermissionError, match="target denied"):
            await env.utu.authorize_conversation(session_id="session", conversation=conversation)


@pytest.mark.asyncio
async def test_global_workflows_are_readable_but_cannot_be_reprovisioned_by_clients():
    row = {"key": "shared", "name": "Shared", "ownership": "global", "definition": {}}
    db = SimpleNamespace(
        find_one=AsyncMock(return_value=row),
        repository=SimpleNamespace(update=AsyncMock(), delete=AsyncMock()),
    )
    svc = WorkflowService(db)
    assert (await svc.get_workflow("shared", USER)).ownership == "global"
    with pytest.raises(WorkflowNotFoundError):
        await svc.update_workflow("shared", WorkflowUpdateRequest(name="spoof"), USER)
    with pytest.raises(WorkflowNotFoundError):
        await svc.delete_workflow("shared", USER)
    db.repository.update.assert_not_awaited()
    db.repository.delete.assert_not_awaited()


def test_inventory_is_a_readonly_projection_without_content_or_assignments():
    rows = [
        {"id": "legacy", "prompt": "secret"},
        {"id": "t", "ownership": "tenant", "tenant_id": "tenant"},
    ]
    result = inventory_ownership(rows, source="coll_agents")
    assert [r["status"] for r in result] == ["unclassified", "tenant"]
    assert result[0]["ownership"] is None
    assert all("prompt" not in row for row in result)
    assert rows[0] == {"id": "legacy", "prompt": "secret"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {"tenant_id": "tenant"},
        {"ownership": "system"},
        {"ownership": "tenant", "tenant_id": "other"},
    ],
)
async def test_unknown_and_foreign_workflows_are_denied_even_to_admin(metadata):
    row = {"key": "old", "name": "Old", "definition": {}, **metadata}
    db = SimpleNamespace(
        find_one=AsyncMock(return_value=row),
        find_all=AsyncMock(return_value=[row]),
        repository=SimpleNamespace(update=AsyncMock(), delete=AsyncMock()),
    )
    svc = WorkflowService(db)
    assert await svc.list_workflows(USER) == []
    with pytest.raises(WorkflowNotFoundError):
        await svc.get_workflow("old", USER)
    with pytest.raises(WorkflowNotFoundError):
        await svc.update_workflow("old", WorkflowUpdateRequest(), USER)
    with pytest.raises(WorkflowNotFoundError):
        await svc.delete_workflow("old", USER)
    db.repository.update.assert_not_awaited()
    db.repository.delete.assert_not_awaited()


def test_workflow_patch_payload_cannot_reclassify():
    req = WorkflowUpdateRequest.model_validate({"ownership": "global", "tenant_id": "other"})
    assert req.model_dump_for_update() == {}


@pytest.mark.asyncio
async def test_v1_agent_reads_global_and_tenant_but_global_mutation_is_denied():
    from src.domains.agentverse.security.agent_access import can_write

    for ownership, tenant in (("global", None), ("tenant", "tenant")):
        row = definition(ownership=ownership, tenant_id=tenant).model_dump()
        db = SimpleNamespace(
            find_by_id=AsyncMock(return_value=row), find_all=AsyncMock(return_value=[row])
        )
        svc = AgentService(Mock(), db, None, None)
        assert (await svc.get_agent(row["id"], USER)).ownership == ownership
        assert len(await svc.list_agents(USER)) == 1
        assert can_write(USER, definition(ownership=ownership, tenant_id=tenant)) is (
            ownership == "tenant"
        )


@pytest.mark.parametrize(
    "inline",
    [
        {"tenant_id": "tenant"},
        {"key": "legacy", "name": "Legacy", "metadata": {}},
    ],
)
def test_inline_resources_cannot_borrow_ownership(inline):
    svc = RegistryService({})
    assert not svc.client_metadata_allowed({"ownership": "global", "expanded": inline}, USER)


@pytest.mark.parametrize("source", ["coll_agents", "registry:tool"])
def test_inventory_cli_hashes_snapshot_without_mutating_it(tmp_path, monkeypatch, capsys, source):
    import hashlib
    import json
    import sys

    from src.domains.agentverse.security.ownership_inventory import main

    rows = [{"key": "old", "prompt": "secret"}]
    if source.startswith("registry:"):
        rows = [{"key": "old", "metadata": {"prompt": "secret"}}]
    snapshot = tmp_path / "snapshot.json"
    content = json.dumps(rows).encode()
    snapshot.write_bytes(content)
    monkeypatch.setattr(sys, "argv", ["inventory", str(snapshot), "--source", source])
    main()
    output = json.loads(capsys.readouterr().out)
    assert output["sha256"] == hashlib.sha256(content).hexdigest()
    assert output["counts"] == {"unclassified": 1}
    assert output["records"][0]["key"] == "old"
    assert "secret" not in json.dumps(output)
    assert snapshot.read_bytes() == content
