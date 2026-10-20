import logging
from typing import Any

from langchain_core.documents import Document

from src.base.infrastructure.db.azure_search.azure_search_client import AzureSearchClient

logger = logging.getLogger(__name__)


class AzureSearchRepository:
    """
    Repository layer for Azure Search operations.
    Provides async-compatible methods wrapping the Azure Search client.
    """

    def __init__(self, azure_search_client: AzureSearchClient):
        """
        Initialize repository with Azure Search client.

        Args:
            azure_search_client: Configured AzureSearchClient instance
        """
        self.azure_search_client = azure_search_client
        logger.info("AzureSearchRepository initialized")

    async def add_documents(
        self,
        documents: list[Document],
        ids: list[str],
        collection_name: str | None = "default",
    ) -> bool:
        """
        Add documents to Azure Search index (async).

        Args:
            documents: List of LangChain Document objects
            ids: List of unique document IDs
            collection_name: Target index name

        Returns:
            True if successful
        """
        return await self.azure_search_client.add_documents(
            documents,
            ids,
            collection_name,
        )

    async def add_embeddings(
        self,
        embeddings: list[list[float]],
        ids: list[str],
        metadatas: list[dict],
        contents: list[str],
        collection_name: str | None = "default",
    ) -> bool:
        """
        Add precomputed embeddings to Azure Search index (async).

        Args:
            embeddings: List of embedding vectors
            ids: List of unique document IDs
            metadatas: List of metadata dictionaries
            contents: List of document content strings
            collection_name: Target index name

        Returns:
            True if successful
        """
        return await self.azure_search_client.add_embeddings(
            embeddings,
            ids,
            metadatas,
            contents,
            collection_name,
        )

    async def search_with_score(
        self,
        query: str,
        collection: str,
        top_k: int = 10,
        metadata_filter: dict | None = None,
        request: Any | None = None,
        query_vector: list[float] | None = None,
    ) -> list[tuple[Document, float]]:
        """
        Perform similarity search with scores (async).

        Args:
            query: Query text
            collection: Index name
            top_k: Number of results to return
            metadata_filter: Optional metadata filters
            request: Optional FastAPI request object
            query_vector: Precomputed query embedding; skips embedding `query`

        Returns:
            List of (Document, score) tuples
        """
        return await self.azure_search_client.similarity_search_with_score(
            query, collection, top_k, metadata_filter, request, query_vector
        )

    def has_document(self, doc_id: str, collection_name: str = "default") -> bool:
        """
        Check if document exists in index (sync).

        Args:
            doc_id: Document ID
            collection_name: Index name

        Returns:
            True if document exists
        """
        return self.azure_search_client.has_document(doc_id, collection_name)

    async def search_by_vector(
        self,
        query_vector: list[float],
        collection_name: str = "default",
        limit: int = 10,
    ) -> list[dict]:
        """
        Search by precomputed vector (async).

        Args:
            query_vector: Query embedding vector
            collection_name: Index name
            limit: Number of results to return

        Returns:
            List of result dictionaries
        """
        return await self.azure_search_client.search_by_vector(
            query_vector,
            collection_name,
            limit,
        )

    async def clear_collection(self, collection_name: str | None = None) -> bool:
        """
        Delete an entire index (async).

        Args:
            collection_name: Index name to delete

        Returns:
            True if successful
        """
        return await self.azure_search_client.clear_collection(
            collection_name,
        )

    async def get_documents(
        self, include: list[str] | None = None, collection_name: str = "default"
    ) -> list[Document]:
        """
        Retrieve all documents from an index (async).

        Args:
            include: Fields to include
            collection_name: Index name

        Returns:
            List of Documents
        """
        return await self.azure_search_client.get_documents(
            include,
            collection_name,
        )

    @property
    def score_mode(self) -> str:
        """Return score mode for compatibility."""
        return self.azure_search_client.score_mode
