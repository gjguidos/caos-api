"""Unit tests for WorkflowService (CRUD, validation, resolution, snapshot)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

# Ensure the tools referenced by the sample workflows are registered.
import src.domains.agentverse.tools.draw_tool  # noqa: F401
import src.domains.agentverse.tools.writer_tool  # noqa: F401
from src.base.entities.user import User
from src.domains.agentverse.entities.workflow import (
    WorkflowCreateRequest,
    WorkflowUpdateRequest,
)
from src.domains.agentverse.exceptions import (
    WorkflowInUseError,
    WorkflowNotFoundError,
    WorkflowValidationError,
)
from src.domains.agentverse.services.db_service import DBService
from src.domains.agentverse.services.workflow_service import WorkflowService
from src.domains.agentverse.tools.workflow_tool.models import WorkflowDefinition


@pytest.fixture(autouse=True)
def explicitly_shared_tool_fixtures(monkeypatch):
    from src.domains.agentverse.registries import tool_registry_instance

    for key in ("writer", "draw"):
        item = tool_registry_instance.get_info(key)
        monkeypatch.setattr(item, "metadata", {**item.metadata, "ownership": "global"})


def _valid_definition() -> dict:
    return {
        "execution_mode": "sequential",
        "steps": [
            {"id": "write", "type": "tool", "command": "@writer --mode=write"},
            {"id": "end", "type": "end", "depends_on": ["write"]},
        ],
    }


def _user() -> User:
    return User(id="user-1", tenant_id="tenant", roles=["agentverse-admin"])


def _make_service() -> tuple[WorkflowService, MagicMock]:
    db_service = MagicMock(spec=DBService)
    db_service.find_one = AsyncMock(return_value=None)
    db_service.find_all = AsyncMock(return_value=[])
    db_service.repository = MagicMock()
    db_service.repository.create = AsyncMock()
    db_service.repository.update = AsyncMock()
    db_service.repository.delete = AsyncMock(return_value=True)
    return WorkflowService(db_service=db_service), db_service


# ----------------------------------------------------------------- create


@pytest.mark.asyncio
async def test_create_slugifies_key_from_name():
    svc, db = _make_service()
    req = WorkflowCreateRequest(name="My Cool Flow", definition=_valid_definition())

    doc = await svc.create_workflow(req, _user())

    assert doc.key == "my_cool_flow"
    assert doc.origin == "db"
    assert doc.created_by == "user-1"
    db.repository.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_rejects_invalid_definition():
    svc, _ = _make_service()
    bad = {"name": "X", "steps": [{"id": "a", "type": "tool", "command": "@no_such_tool_zzz"}]}
    req = WorkflowCreateRequest(name="bad", definition=bad)

    with pytest.raises(WorkflowValidationError):
        await svc.create_workflow(req, _user())


@pytest.mark.asyncio
async def test_create_rejects_duplicate_key():
    svc, db = _make_service()
    db.find_one = AsyncMock(
        return_value={"key": "mini", "ownership": "tenant", "tenant_id": "tenant"}
    )
    req = WorkflowCreateRequest(name="mini", definition=_valid_definition())

    with pytest.raises(WorkflowValidationError):
        await svc.create_workflow(req, _user())


@pytest.mark.asyncio
async def test_create_derives_required_tools_from_the_dag():
    """The stored list is what the agent factory equips at workflow scope, so it
    has to cover the steps even when the author declared nothing."""
    svc, _ = _make_service()
    req = WorkflowCreateRequest(name="mini", definition=_valid_definition())

    doc = await svc.create_workflow(req, _user())

    assert doc.required_tools == ["writer"]


@pytest.mark.asyncio
async def test_create_ignores_a_declared_tool_list():
    """``required_tools`` is derived, never declared. A caller cannot equip attached
    agents with a tool the DAG never invokes by asking for it."""
    svc, _ = _make_service()
    req = WorkflowCreateRequest.model_validate(
        {"name": "mini", "definition": _valid_definition(), "required_tools": ["draw"]}
    )

    doc = await svc.create_workflow(req, _user())

    assert doc.required_tools == ["writer"]


# ----------------------------------------------------------------- update


@pytest.mark.asyncio
async def test_update_rejects_missing():
    svc, db = _make_service()
    db.find_one = AsyncMock(return_value=None)

    with pytest.raises(WorkflowNotFoundError):
        await svc.update_workflow("ghost", WorkflowUpdateRequest(name="x"), _user())


@pytest.mark.asyncio
async def test_update_applies_only_set_fields():
    svc, db = _make_service()
    existing = {
        "key": "mini",
        "ownership": "tenant",
        "tenant_id": "tenant",
        "name": "Mini",
        "definition": _valid_definition(),
        "required_tools": ["writer"],
        "origin": "builtin",
    }
    # find_one: first the lookup, then the refreshed read.
    db.find_one = AsyncMock(side_effect=[existing, {**existing, "name": "Renamed"}])

    doc = await svc.update_workflow("mini", WorkflowUpdateRequest(name="Renamed"), _user())

    assert doc.name == "Renamed"
    # key/origin are not part of the update payload.
    args, kwargs = db.repository.update.call_args
    update_payload = kwargs["data"]
    assert "key" not in update_payload
    assert "origin" not in update_payload
    assert update_payload["updated_by"] == "user-1"


@pytest.mark.asyncio
async def test_definition_only_edit_widens_required_tools():
    """A step added by a definition-only patch must widen the stored tool set -
    otherwise the declaration silently drifts from the DAG it describes."""
    svc, db = _make_service()
    existing = {
        "key": "mini",
        "ownership": "tenant",
        "tenant_id": "tenant",
        "name": "Mini",
        "definition": _valid_definition(),
        "required_tools": ["writer"],
    }
    widened = {
        "execution_mode": "sequential",
        "steps": [
            {"id": "write", "type": "tool", "command": "@writer --mode=write"},
            {"id": "pic", "type": "tool", "command": "@draw a cat", "depends_on": ["write"]},
            {"id": "end", "type": "end", "depends_on": ["pic"]},
        ],
    }
    db.find_one = AsyncMock(side_effect=[existing, {**existing, "definition": widened}])

    await svc.update_workflow("mini", WorkflowUpdateRequest(definition=widened), _user())

    _args, kwargs = db.repository.update.call_args
    assert kwargs["data"]["required_tools"] == ["draw", "writer"]


# ----------------------------------------------------------------- delete


@pytest.mark.asyncio
async def test_delete_blocked_when_pinned():
    svc, db = _make_service()
    db.find_one = AsyncMock(
        return_value={"key": "mini", "ownership": "tenant", "tenant_id": "tenant"}
    )
    db.find_all = AsyncMock(return_value=[{"id": "agent-1"}, {"id": "agent-2"}])

    with pytest.raises(WorkflowInUseError):
        await svc.delete_workflow("mini", _user())
    db.repository.delete.assert_not_called()


@pytest.mark.asyncio
async def test_delete_succeeds_when_unpinned():
    svc, db = _make_service()
    db.find_one = AsyncMock(
        return_value={"key": "mini", "ownership": "tenant", "tenant_id": "tenant"}
    )
    db.find_all = AsyncMock(return_value=[])

    await svc.delete_workflow("mini", _user())
    db.repository.delete.assert_awaited_once()


# ------------------------------------------------- required_tools_for


def _row(key: str, required_tools: list[str]) -> dict:
    return {
        "key": key,
        "ownership": "tenant",
        "tenant_id": "tenant",
        "name": key,
        "definition": _valid_definition(),
        "required_tools": required_tools,
    }


@pytest.mark.asyncio
async def test_required_tools_for_unions_across_workflows():
    svc, db = _make_service()
    db.find_all = AsyncMock(
        return_value=[_row("wf_a", ["writer"]), _row("wf_b", ["draw", "writer"])]
    )

    assert await svc.required_tools_for(["wf_a", "wf_b"]) == {"writer", "draw"}


@pytest.mark.asyncio
async def test_required_tools_for_fetches_every_key_in_one_query():
    """One `$in` round-trip, not one per key - this is the only caller that runs
    on the agent-save path, where the key set is unbounded."""
    svc, db = _make_service()
    db.find_all = AsyncMock(return_value=[_row("wf_a", ["writer"])])

    await svc.required_tools_for(["wf_a", "wf_b"])

    db.find_all.assert_awaited_once()
    query, _kwargs = db.find_all.await_args
    assert query[0] == {"key": {"$in": ["wf_a", "wf_b"]}}


@pytest.mark.asyncio
async def test_required_tools_for_skips_a_deleted_workflow():
    """An agent attached to a since-deleted workflow keeps its others runnable."""
    svc, db = _make_service()
    db.find_all = AsyncMock(return_value=[_row("wf_a", ["writer"])])

    assert await svc.required_tools_for(["wf_a", "gone"]) == {"writer"}


@pytest.mark.asyncio
async def test_get_workflows_reports_missing_keys_separately():
    """The (found, missing) split is what lets attach time reject a key that run
    time merely skips."""
    svc, db = _make_service()
    db.find_all = AsyncMock(return_value=[_row("wf_a", ["writer"])])

    found, missing = await svc.get_workflows(["wf_a", "gone"])

    assert [wf.key for wf in found] == ["wf_a"]
    assert missing == ["gone"]


@pytest.mark.asyncio
async def test_get_workflows_slugifies_and_deduplicates_keys():
    svc, db = _make_service()
    db.find_all = AsyncMock(return_value=[_row("my_flow", [])])

    found, missing = await svc.get_workflows(["My Flow", "my_flow", ""])

    query, _kwargs = db.find_all.await_args
    assert query[0] == {"key": {"$in": ["my_flow"]}}
    assert [wf.key for wf in found] == ["my_flow"]
    assert missing == []


@pytest.mark.asyncio
async def test_required_tools_for_no_workflows_requires_nothing():
    svc, _ = _make_service()

    assert await svc.required_tools_for([]) == set()


# ----------------------------------------------------------------- resolve


@pytest.mark.asyncio
async def test_resolve_definition_from_mongo():
    svc, db = _make_service()
    db.find_one = AsyncMock(
        return_value={
            "key": "mini",
            "ownership": "tenant",
            "tenant_id": "tenant",
            "name": "Mini",
            "definition": _valid_definition(),
        }
    )

    wf, name, inputs = await svc.resolve_definition("mini")
    assert isinstance(wf, WorkflowDefinition)
    assert name == "Mini"
    assert inputs == []
    assert not hasattr(wf, "name")


@pytest.mark.asyncio
async def test_resolve_definition_returns_declared_inputs():
    svc, db = _make_service()
    db.find_one = AsyncMock(
        return_value={
            "key": "mini",
            "ownership": "tenant",
            "tenant_id": "tenant",
            "name": "Mini",
            "definition": _valid_definition(),
            "inputs": [{"key": "pic", "label": "Picture", "input": "file", "required": True}],
        }
    )

    _wf, _name, inputs = await svc.resolve_definition("mini")
    assert len(inputs) == 1
    assert inputs[0].key == "pic"
    assert inputs[0].is_file


@pytest.mark.asyncio
async def test_resolve_definition_missing_raises():
    svc, db = _make_service()
    db.find_one = AsyncMock(return_value=None)

    with pytest.raises(WorkflowNotFoundError):
        await svc.resolve_definition("ghost")


# ----------------------------------------------------------------- list/get


@pytest.mark.asyncio
async def test_list_has_no_auth_filter():
    svc, db = _make_service()
    db.find_all = AsyncMock(
        return_value=[
            {"ownership": "global", "key": "a", "name": "A", "definition": _valid_definition()},
            {"ownership": "global", "key": "b", "name": "B", "definition": _valid_definition()},
        ]
    )

    docs = await svc.list_workflows(_user())
    assert {d.key for d in docs} == {"a", "b"}
    # find_all called with the workflows collection and no query.
    _, kwargs = db.find_all.call_args
    assert kwargs.get("collection_name") == DBService.WORKFLOWS_COL
