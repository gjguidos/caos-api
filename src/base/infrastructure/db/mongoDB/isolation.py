"""Isolated MongoDB databases for integration tests; never the configured application DB.

Each test gets its own ``caos_test_<label>_<uuid>`` database on the configured local
Mongo server and the whole database is dropped afterwards, so a collection leaked by
an interrupted test cannot survive. The guard fails closed before any connection if a
name could address the application database (e.g. ``caos``), a Mongo system database
or anything without the test prefix, and it refuses Azure Cosmos endpoints outright.

Test support only: application code must keep using ``DatabaseContainer``.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from src.base.config.config import settings
from src.base.infrastructure.db.mongoDB.mongo_client import MongoDBClient
from src.base.repositories.mongodb_repository import MongoDBRepository

TEST_DATABASE_PREFIX = "caos_test_"
# Never valid test targets, whatever DB_NAME says.
PROTECTED_DATABASES = frozenset({"caos", "admin", "config", "local", "agentverse_local"})
_LABEL = re.compile(r"[a-z][a-z0-9]{0,15}")
_NAME = re.compile(rf"{TEST_DATABASE_PREFIX}[a-z][a-z0-9]{{0,15}}_[0-9a-f]{{32}}")


class UnsafeTestDatabase(RuntimeError):
    """An integration test addressed a database it must never touch."""


def disposable_database_name(label: str) -> str:
    if not _LABEL.fullmatch(label):
        raise UnsafeTestDatabase("Test database labels are short lowercase identifiers")
    return f"{TEST_DATABASE_PREFIX}{label}_{uuid4().hex}"


def require_test_database(name: str) -> str:
    """Fail closed unless ``name`` is a generated, disposable test database."""
    configured = settings.database.db_name
    if (
        not isinstance(name, str)
        or name in PROTECTED_DATABASES
        or name == configured
        or not _NAME.fullmatch(name)
    ):
        raise UnsafeTestDatabase(f"Refusing to use {name!r} as an integration-test database")
    if settings.database.use_azure_cosmos:
        raise UnsafeTestDatabase("Integration tests never run against Azure Cosmos")
    return name


@asynccontextmanager
async def isolated_mongo_client(label: str) -> AsyncIterator[MongoDBClient]:
    """A connected-on-demand client for one disposable database, dropped on exit."""
    name = require_test_database(disposable_database_name(label))
    client = MongoDBClient(settings.database.resolved_uri, name)
    try:
        yield client
    finally:
        try:
            if client.client is not None:
                # Re-check before the only destructive call this helper makes.
                await client.client.drop_database(require_test_database(client.db_name))
        finally:
            await client.disconnect()


@asynccontextmanager
async def isolated_mongo_repository(label: str) -> AsyncIterator[MongoDBRepository]:
    async with isolated_mongo_client(label) as client:
        yield MongoDBRepository(client=client)
