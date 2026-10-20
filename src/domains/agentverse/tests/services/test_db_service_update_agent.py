"""``DBService.update_agent`` filters the payload through a mutable-field allowlist.

Any agent field the API accepts on update has to be listed there, or the write is
silently dropped and the caller still gets a success response.
"""

from __future__ import annotations

from typing import Any

import pytest

from src.domains.agentverse.services.db_service import DBService


class FakeRepository:
    def __init__(self, agent: dict[str, Any]):
        self.agent = agent
        self.last_update: dict[str, Any] | None = None

    async def find_one(self, query, collection_name):  # noqa: ANN001, ANN201
        return self.agent

    async def update(self, query, data, collection_name):  # noqa: ANN001, ANN201
        self.last_update = data
        self.agent = {**self.agent, **data}
        return True


@pytest.fixture
def repository() -> FakeRepository:
    return FakeRepository({"id": "agent-1", "name": "Rei", "workflows": ["mini_workflow"]})


@pytest.fixture
def service(repository: FakeRepository) -> DBService:
    return DBService(mongodb_repository=repository)


class TestUpdateAgentWorkflows:
    @pytest.mark.asyncio
    async def test_attaching_a_workflow_is_persisted(self, service, repository):
        updated = await service.update_agent("agent-1", {"workflows": ["nested_mini_workflow"]})

        assert repository.last_update["workflows"] == ["nested_mini_workflow"]
        assert updated["workflows"] == ["nested_mini_workflow"]

    @pytest.mark.asyncio
    async def test_clearing_workflows_is_persisted(self, service, repository):
        updated = await service.update_agent("agent-1", {"workflows": []})

        assert repository.last_update["workflows"] == []
        assert updated["workflows"] == []

    @pytest.mark.asyncio
    async def test_embedding_type_is_persisted(self, service, repository):
        updated = await service.update_agent(
            "agent-1", {"embedding_type": "text-embedding-ada-002-stig"}
        )

        assert repository.last_update["embedding_type"] == "text-embedding-ada-002-stig"
        assert updated["embedding_type"] == "text-embedding-ada-002-stig"

    @pytest.mark.asyncio
    async def test_unknown_fields_are_still_dropped(self, service, repository):
        await service.update_agent("agent-1", {"id": "hijacked", "creator": "someone-else"})

        assert repository.last_update is None
