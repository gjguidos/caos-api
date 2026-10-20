"""Tests for builtin workflow seeding (WS-F)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.domains.agentverse.services.db_service import DBService
from src.domains.agentverse.workflows.seed import seed_builtin_workflows


def _db() -> MagicMock:
    db = MagicMock(spec=DBService)
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
