"""``VectorDBService`` is the single place that decides which embedding backend a
collection uses. Ingestion and retrieval must agree, or documents are written into
one vector space and searched in another - which fails silently, with plausible but
meaningless results.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.domains.agentverse.entities.collection import CollectionDocument
from src.domains.agentverse.services.vectordb_service import VectorDBService


class FakeEmbeddingRepo:
    def __init__(self, key: str):
        self.key = key

    async def get_embeddings_async(self, texts):  # noqa: ANN001, ANN201
        # A vector that identifies which backend produced it.
        return [[float(len(self.key))] for _ in texts]


class FakeVectorRepo:
    def __init__(self):
        self.last_search: dict | None = None

    async def search_with_score(self, **kwargs):  # noqa: ANN003, ANN201
        self.last_search = kwargs
        return []


class FakeDBService:
    def __init__(self, collection: CollectionDocument | None = None):
        self.collection = collection

    async def find_collection_by_name(self, collection_name):  # noqa: ANN001, ANN201
        return self.collection


def _request(vector_repo=None):
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                cognitive_modules={
                    "knowledge_db": {"chromadb": vector_repo or FakeVectorRepo()},
                    "embedding": {
                        "default-embed": FakeEmbeddingRepo("default-embed"),
                        "special-embedding": FakeEmbeddingRepo("special-embedding"),
                    },
                },
                cognitive_defaults={"knowledge_db": "chromadb", "embedding": "default-embed"},
            )
        )
    )


@pytest.fixture
def service() -> VectorDBService:
    return VectorDBService(db_service=FakeDBService())


class TestResolveRepos:
    def test_none_falls_back_to_the_global_default(self, service):
        _, embedding_repo = service._resolve_repos(_request(), None)

        assert embedding_repo.key == "default-embed"

    def test_known_key_is_honoured(self, service):
        _, embedding_repo = service._resolve_repos(_request(), "special-embedding")

        assert embedding_repo.key == "special-embedding"

    def test_unknown_key_raises_instead_of_silently_substituting(self, service):
        """Falling back here would search a vector space the documents are not in."""
        with pytest.raises(ValueError, match="embedding backend"):
            service._resolve_repos(_request(), "retired-model")


class TestEmbeddingKeyForCollection:
    @pytest.mark.asyncio
    async def test_returns_the_stamped_model(self):
        col = CollectionDocument(
            name="handbook",
            agent_id="agent-1",
            owner_id="user-1",
            embedding_model="special-embedding",
        )
        service = VectorDBService(db_service=FakeDBService(col))

        assert await service.embedding_key_for_collection("handbook") == "special-embedding"

    @pytest.mark.asyncio
    async def test_unknown_collection_means_the_default(self, service):
        assert await service.embedding_key_for_collection("nope") is None


class TestSearchWithScore:
    @pytest.mark.asyncio
    async def test_query_is_embedded_with_the_collections_backend(self, service):
        vector_repo = FakeVectorRepo()

        await service.search_with_score(
            request=_request(vector_repo),
            collection="handbook",
            query="what is the policy?",
            top_k=7,
            metadata_filter={"user_id": "user-1"},
            embedding_key="special-embedding",
        )

        assert vector_repo.last_search["query_vector"] == [float(len("special-embedding"))]
        assert vector_repo.last_search["collection"] == "handbook"
        assert vector_repo.last_search["top_k"] == 7
        assert vector_repo.last_search["metadata_filter"] == {"user_id": "user-1"}

    @pytest.mark.asyncio
    async def test_falls_back_to_the_default_backend(self, service):
        vector_repo = FakeVectorRepo()

        await service.search_with_score(
            request=_request(vector_repo),
            collection="handbook",
            query="what is the policy?",
        )

        assert vector_repo.last_search["query_vector"] == [float(len("default-embed"))]
