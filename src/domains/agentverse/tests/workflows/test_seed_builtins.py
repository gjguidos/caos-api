"""Tests for builtin workflow seeding (WS-F)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domains.agentverse.services.db_service import DBService
from src.domains.agentverse.workflows.seed import seed_builtin_workflows


def _db() -> MagicMock:
    db = MagicMock(spec=DBService)
    db.find_one = AsyncMock(return_value=None)
    db.repository = MagicMock()
    db.repository.upsert = AsyncMock()
    collection = MagicMock()
    collection.create_index = AsyncMock()
    db.repository.get_collection = AsyncMock(return_value=collection)
    return db


@pytest.mark.asyncio
async def test_seed_upserts_all_workflows():
    db = _db()
    seeded = await seed_builtin_workflows(db)

    assert seeded >= 2
    assert db.repository.upsert.await_count == seeded
    # All seeded rows are stamped origin=builtin.
    for call in db.repository.upsert.await_args_list:
        doc = call.kwargs["data"]
        assert doc["origin"] == "builtin"
        assert doc["ownership"] == "global"


@pytest.mark.asyncio
async def test_seed_upserts_existing_workflows():
    # Builtin workflows are always upserted, so updates to the spec
    # are automatically reflected in the database.
    from src.domains.agentverse.entities.workflow import slugify_workflow_key
    from src.domains.agentverse.workflows.builtins import BUILTIN_WORKFLOWS

    db = _db()
    seeded = await seed_builtin_workflows(db)

    # Should upsert every builtin, regardless of whether they exist.
    total_builtins = len(BUILTIN_WORKFLOWS)
    assert seeded == total_builtins
    assert db.repository.upsert.await_count == total_builtins

    # Verify upsert was called with the correct key queries.
    keys = {slugify_workflow_key(f().key) for f in BUILTIN_WORKFLOWS}
    upserted_keys = {call.kwargs["query"]["key"] for call in db.repository.upsert.await_args_list}
    assert upserted_keys == keys


@pytest.mark.asyncio
async def test_seed_ensures_unique_index():
    db = _db()
    await seed_builtin_workflows(db)

    collection = await db.repository.get_collection(DBService.WORKFLOWS_COL)
    collection.create_index.assert_awaited_with("key", unique=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "existing",
    [
        {"origin": "builtin"},
        {"ownership": "tenant", "tenant_id": "tenant", "origin": "db"},
        {"ownership": "system", "origin": "builtin"},
        {"ownership": "global", "tenant_scoped": True, "origin": "builtin"},
        {"ownership": "global", "tenant_id": "tenant", "origin": "builtin"},
    ],
)
async def test_seed_preserves_unclassified_or_conflicting_history(existing):
    db = _db()
    db.find_one.return_value = existing
    assert await seed_builtin_workflows(db) == 0
    db.repository.upsert.assert_not_awaited()


@pytest.mark.asyncio
async def test_seed_updates_only_explicitly_shared_builtin_rows():
    db = _db()
    db.find_one.return_value = {"ownership": "global", "origin": "builtin"}
    assert await seed_builtin_workflows(db) >= 2
    for call in db.repository.upsert.await_args_list:
        assert call.kwargs["query"]["ownership"] == "global"
        assert call.kwargs["query"]["origin"] == "builtin"
        assert "ownership" not in call.kwargs["data"]
        assert "exposure" not in call.kwargs["data"]


@pytest.mark.asyncio
async def test_seed_does_not_retry_a_concurrent_ownership_collision():
    from pymongo.errors import DuplicateKeyError

    db = _db()
    db.repository.upsert.side_effect = DuplicateKeyError("collision")
    assert await seed_builtin_workflows(db) == 0
    for call in db.repository.upsert.await_args_list:
        assert call.kwargs["query"]["ownership"] == "global"
