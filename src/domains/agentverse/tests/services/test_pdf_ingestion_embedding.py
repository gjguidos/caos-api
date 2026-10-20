"""A collection's embedding model is stamped when it is created and is immutable
afterwards: mixing two models in one collection makes its documents unretrievable.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.domains.agentverse.entities.collection import CollectionCreateMetadata, CollectionDocument
from src.domains.agentverse.exceptions import PDFIngestionError
from src.domains.agentverse.services.pdf_ingestion_orchestration_service import (
    PDFIngestionOrchestrator,
)


class FakeDBService:
    def __init__(self, agent: dict, collection: CollectionDocument | None):
        self.agent = agent
        self.collection = collection
        self.upserted: CollectionDocument | None = None

    async def find_by_id(self, agent_id):  # noqa: ANN001, ANN201
        return self.agent

    async def find_collection(self, agent_id, collection_name):  # noqa: ANN001, ANN201
        return self.collection

    async def upsert_collection(self, col_doc):  # noqa: ANN001, ANN201
        self.upserted = col_doc
        return col_doc

    async def add_collection_to_agent(self, agent_id, collection_name):  # noqa: ANN001, ANN201
        return None


def _request():
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(cognitive_defaults={"embedding": "default-embed"})
        )
    )


def _orchestrator(agent: dict, collection: CollectionDocument | None) -> PDFIngestionOrchestrator:
    return PDFIngestionOrchestrator(
        pdf_ingestion_service=None,
        db_service=FakeDBService(agent, collection),
        vectordb_service=None,
    )


def _collection(**overrides) -> CollectionDocument:
    base = {
        "name": "handbook",
        "agent_id": "agent-1",
        "owner_id": "user-1",
        "visibility": "public",
    }
    return CollectionDocument(**{**base, **overrides})


async def _ingest(orchestrator: PDFIngestionOrchestrator):
    return await orchestrator.ingest_pdf(
        _request(),
        agent_id="agent-1",
        user_id="user-1",
        collection_name="handbook",
        pdf_base64=None,
        metadata=CollectionCreateMetadata(),
    )


class TestEmbeddingStamping:
    @pytest.mark.asyncio
    async def test_new_collection_records_the_agents_embedding(self):
        orchestrator = _orchestrator({"embedding_type": "special-embedding"}, None)

        await _ingest(orchestrator)

        assert orchestrator.db_service.upserted.embedding_model == "special-embedding"

    @pytest.mark.asyncio
    async def test_agent_without_an_embedding_records_the_default(self):
        orchestrator = _orchestrator({"id": "agent-1"}, None)

        await _ingest(orchestrator)

        assert orchestrator.db_service.upserted.embedding_model == "default-embed"

    @pytest.mark.asyncio
    async def test_mismatched_agent_is_rejected(self):
        orchestrator = _orchestrator(
            {"embedding_type": "special-embedding"},
            _collection(embedding_model="default-embed"),
        )

        with pytest.raises(PDFIngestionError, match="embedded with"):
            await _ingest(orchestrator)

    @pytest.mark.asyncio
    async def test_unstamped_collection_matches_a_default_agent(self):
        """Legacy collections were embedded with the default, so this is not a mismatch."""
        orchestrator = _orchestrator({"embedding_type": "default-embed"}, _collection())

        await _ingest(orchestrator)  # does not raise
