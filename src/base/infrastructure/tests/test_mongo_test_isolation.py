"""Integration tests must never address the application database (e.g. ``caos``)."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from src.base.infrastructure.db.mongoDB import isolation
from src.base.infrastructure.db.mongoDB.isolation import (
    PROTECTED_DATABASES,
    UnsafeTestDatabase,
    disposable_database_name,
    isolated_mongo_client,
    isolated_mongo_repository,
    require_test_database,
)


def configured(monkeypatch: pytest.MonkeyPatch, *, db_name: str, cosmos: bool = False) -> None:
    database = SimpleNamespace(
        db_name=db_name, use_azure_cosmos=cosmos, resolved_uri="mongodb://unused"
    )
    monkeypatch.setattr(isolation, "settings", SimpleNamespace(database=database))


@pytest.mark.parametrize(
    "name",
    [
        "caos",
        "admin",
        "config",
        "local",
        "agentverse_local",
        "",
        "caos_test_",
        "caos_test_ops",  # no unique suffix
        "g14_test_" + "a" * 32,  # legacy unprefixed name
        "caos_test_ops_" + "A" * 32,
        "caos_test_ops_" + "a" * 32 + "x",
        "caos_test_UPPER_" + "a" * 32,
    ],
)
def test_guard_rejects_application_system_and_unprefixed_databases(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    configured(monkeypatch, db_name="caos")
    with pytest.raises(UnsafeTestDatabase):
        require_test_database(name)


def test_guard_rejects_the_configured_database_even_if_it_looks_disposable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    name = disposable_database_name("ops")
    configured(monkeypatch, db_name=name)
    with pytest.raises(UnsafeTestDatabase):
        require_test_database(name)


def test_guard_refuses_azure_cosmos(monkeypatch: pytest.MonkeyPatch) -> None:
    configured(monkeypatch, db_name="caos", cosmos=True)
    with pytest.raises(UnsafeTestDatabase, match="Cosmos"):
        require_test_database(disposable_database_name("ops"))


def test_generated_names_are_unique_bounded_and_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    configured(monkeypatch, db_name="caos")
    first, second = disposable_database_name("ops4c"), disposable_database_name("ops4c")
    assert first != second and first.startswith("caos_test_ops4c_")
    assert len(first) <= 64  # MongoDB database name limit
    assert require_test_database(first) == first
    assert "caos" in PROTECTED_DATABASES
    for label in ("", "Ops", "ops-4", "x" * 17):
        with pytest.raises(UnsafeTestDatabase):
            disposable_database_name(label)


@pytest.mark.asyncio
async def test_unsafe_configuration_fails_before_any_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configured(monkeypatch, db_name="caos", cosmos=True)
    with pytest.raises(UnsafeTestDatabase):
        async with isolated_mongo_client("ops"):
            pytest.fail("No client may be yielded for an unsafe target")


@pytest.mark.asyncio
async def test_isolated_database_is_dropped_whole_even_when_the_test_fails() -> None:
    if os.environ.get("OPS2_MONGO_INTEGRATION") != "1":
        pytest.skip("Set OPS2_MONGO_INTEGRATION=1 for real Mongo isolation tests")
    with pytest.raises(RuntimeError, match="interrupted"):
        async with isolated_mongo_repository("guard") as repository:
            name = repository.client.db_name
            # Two collections, one deliberately never dropped by the "test".
            await (await repository.get_collection("leaked")).insert_one({"probe": True})
            await (await repository.get_collection("other")).insert_one({"probe": True})
            server = repository.client.client
            assert name in await server.list_database_names()
            raise RuntimeError("interrupted test")
    from motor.motor_asyncio import AsyncIOMotorClient

    from src.base.config.config import settings

    probe = AsyncIOMotorClient(settings.database.resolved_uri)
    try:
        names = await probe.list_database_names()
        assert name not in names
        # The application database never gained a test collection from this helper.
        if settings.database.db_name in names:
            collections = await probe[settings.database.db_name].list_collection_names()
            assert not any(c in ("leaked", "other") for c in collections)
    finally:
        probe.close()
