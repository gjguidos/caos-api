import json
import logging
from collections.abc import Callable
from typing import Any

from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import ResourceNotFoundError
from azure.identity.aio import DefaultAzureCredential
from azure.search.documents.aio import SearchClient
from azure.search.documents.indexes.aio import SearchIndexClient
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    HnswParameters,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    VectorSearch,
    VectorSearchProfile,
)
from azure.search.documents.models import VectorizedQuery
from langchain_core.documents import Document

logger = logging.getLogger(__name__)


class AzureSearchClient:
    """
    Async Azure AI Search client that provides a ChromaDB-compatible interface.
    Supports vector similarity search with LangChain Document objects.
    """

    def __init__(
        self,
        endpoint: str,
        key: str | None = None,
        embedding_function: Callable | None = None,
        vector_dimensions: int = 1536,
        index_prefix: str = "agentverse",
    ):
        """
        Initialize Azure Search client.

        Args:
            endpoint: Azure Search service endpoint URL
            key: Azure Search admin API key (if None, uses DefaultAzureCredential)
            embedding_function: Function to generate embeddings (LangChain Embeddings object)
            vector_dimensions: Dimension of embedding vectors
            index_prefix: Prefix for all index names
        """
        self.endpoint = endpoint
        self.embedding_function = embedding_function
        self.vector_dimensions = vector_dimensions
        self.index_prefix = index_prefix

        # Use key credential or Azure AD credential
        if key:
            self.credential = AzureKeyCredential(key)
        else:
            self.credential = DefaultAzureCredential()

        self.index_client = SearchIndexClient(endpoint=self.endpoint, credential=self.credential)

        # Cache search clients per index
        self._search_client_cache: dict[str, SearchClient] = {}

        logger.info(
            f"AzureSearchClient initialized; endpoint: {endpoint}, vector_dims: {vector_dimensions}"
        )

    async def __aenter__(self):
        """Async context manager entry."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit - cleanup resources."""
        await self.close()

    async def close(self):
        """Close all clients and cleanup resources."""
        # Close all cached search clients
        for client in self._search_client_cache.values():
            await client.close()
        self._search_client_cache.clear()

        # Close index client
        await self.index_client.close()

        # Close credential if it's async
        if hasattr(self.credential, "close"):
            await self.credential.close()

    def _get_index_name(self, collection_name: str) -> str:
        """Get full index name with prefix."""
        if not collection_name:
            collection_name = "default"
        return f"{self.index_prefix}-{collection_name}".lower()

    def _get_search_client(self, collection_name: str) -> SearchClient:
        """Get or create cached search client for an index."""
        index_name = self._get_index_name(collection_name)

        if index_name not in self._search_client_cache:
            self._search_client_cache[index_name] = SearchClient(
                endpoint=self.endpoint, index_name=index_name, credential=self.credential
            )

        return self._search_client_cache[index_name]

    async def _ensure_index_exists(self, collection_name: str) -> None:
        """Create index if it doesn't exist."""
        index_name = self._get_index_name(collection_name)

        try:
            # Check if index exists
            await self.index_client.get_index(index_name)
            logger.debug(f"Index '{index_name}' already exists")
        except ResourceNotFoundError:
            # Create new index
            logger.info(f"Creating index '{index_name}'")

            fields = [
                SearchField(
                    name="id",
                    type=SearchFieldDataType.String,
                    key=True,
                    filterable=True,
                ),
                SearchField(
                    name="content",
                    type=SearchFieldDataType.String,
                    searchable=True,
                ),
                SearchField(
                    name="content_vector",
                    type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                    searchable=True,
                    vector_search_dimensions=self.vector_dimensions,
                    vector_search_profile_name="default-profile",
                ),
                SearchField(
                    name="metadata",
                    type=SearchFieldDataType.String,
                    searchable=False,
                    filterable=True,
                ),
                SearchField(
                    name="source",
                    type=SearchFieldDataType.String,
                    searchable=True,
                    filterable=True,
                ),
                SearchField(
                    name="page",
                    type=SearchFieldDataType.String,
                    searchable=False,
                    filterable=True,
                ),
                SearchField(
                    name="user_id",
                    type=SearchFieldDataType.String,
                    searchable=False,
                    filterable=True,
                ),
                SearchField(
                    name="agent_id",
                    type=SearchFieldDataType.String,
                    searchable=False,
                    filterable=True,
                ),
            ]

            vector_search = VectorSearch(
                profiles=[
                    VectorSearchProfile(
                        name="default-profile",
                        algorithm_configuration_name="hnsw-config",
                    )
                ],
                algorithms=[
                    HnswAlgorithmConfiguration(
                        name="hnsw-config",
                        parameters=HnswParameters(
                            metric="cosine",
                            m=4,
                            ef_construction=400,
                            ef_search=500,
                        ),
                    )
                ],
            )

            index = SearchIndex(
                name=index_name,
                fields=fields,
                vector_search=vector_search,
            )

            await self.index_client.create_index(index)
            logger.info(f"Created index '{index_name}'")

    async def add_documents(
        self,
        documents: list[Document],
        ids: list[str],
        collection_name: str | None = "default",
    ) -> bool:
        """
        Add documents to the index (mimics ChromaDB interface).

        Args:
            documents: List of LangChain Document objects
            ids: List of unique document IDs
            collection_name: Target index name

        Returns:
            True if successful
        """
        try:
            await self._ensure_index_exists(collection_name)
            search_client = self._get_search_client(collection_name)

            # Generate embeddings for all documents
            texts = [doc.page_content for doc in documents]
            if self.embedding_function:
                # Check if embedding function is async
                if hasattr(self.embedding_function, "aembed_documents"):
                    embeddings = await self.embedding_function.aembed_documents(texts)
                else:
                    embeddings = self.embedding_function.embed_documents(texts)
            else:
                raise ValueError("Embedding function is required for add_documents")

            # Prepare documents for upload
            azure_docs = []
            for doc_id, doc, embedding in zip(ids, documents, embeddings):
                metadata = doc.metadata or {}

                azure_doc = {
                    "id": doc_id,
                    "content": doc.page_content,
                    "content_vector": embedding,
                    "metadata": json.dumps(metadata),
                    "source": metadata.get("source", ""),
                    "page": str(metadata.get("page", "")),
                    "user_id": metadata.get("user_id", ""),
                    "agent_id": metadata.get("agent_id", ""),
                }
                azure_docs.append(azure_doc)

            # Upload documents
            result = await search_client.upload_documents(documents=azure_docs)
            logger.info(f"Added {len(documents)} documents to index '{collection_name}'")
            return True

        except Exception:
            logger.exception(f"Failed to add documents to Azure Search index '{collection_name}'")
            return False

    async def add_embeddings(
        self,
        embeddings: list[list[float]],
        ids: list[str],
        metadatas: list[dict],
        contents: list[str],
        collection_name: str | None = "default",
    ) -> bool:
        """
        Add precomputed embeddings (mimics ChromaDB interface).

        Args:
            embeddings: List of embedding vectors
            ids: List of unique document IDs
            metadatas: List of metadata dictionaries
            contents: List of document content strings
            collection_name: Target index name

        Returns:
            True if successful
        """
        try:
            await self._ensure_index_exists(collection_name)
            search_client = self._get_search_client(collection_name)

            # Prepare documents for upload
            azure_docs = []
            for doc_id, embedding, metadata, content in zip(ids, embeddings, metadatas, contents):
                azure_doc = {
                    "id": doc_id,
                    "content": content,
                    "content_vector": embedding,
                    "metadata": json.dumps(metadata or {}),
                    "source": metadata.get("source", "") if metadata else "",
                    "page": str(metadata.get("page", "")) if metadata else "",
                    "user_id": metadata.get("user_id", "") if metadata else "",
                    "agent_id": metadata.get("agent_id", "") if metadata else "",
                }
                azure_docs.append(azure_doc)

            # Upload documents
            result = await search_client.upload_documents(documents=azure_docs)
            logger.info(f"Added {len(embeddings)} embeddings to index '{collection_name}'")
            return True

        except Exception:
            logger.exception(f"Failed to add embeddings to Azure Search index '{collection_name}'")
            return False

    async def similarity_search_with_score(
        self,
        query: str,
        collection: str,
        k: int = 5,
        filter: dict | None = None,
        request: Any | None = None,
        query_vector: list[float] | None = None,
    ) -> list[tuple[Document, float]]:
        """
        Perform similarity search with scores (mimics ChromaDB interface).

        Args:
            query: Query text
            collection: Index name
            k: Number of results to return
            filter: Optional metadata filters
            request: Optional FastAPI request object
            query_vector: Precomputed query embedding; skips embedding `query`

        Returns:
            List of (Document, score) tuples
        """
        try:
            search_client = self._get_search_client(collection)

            if query_vector is None:
                if not self.embedding_function:
                    raise ValueError("Embedding function is required for search")
                # Check if embedding function is async
                if hasattr(self.embedding_function, "aembed_query"):
                    query_vector = await self.embedding_function.aembed_query(query)
                else:
                    query_vector = self.embedding_function.embed_query(query)

            # Create vectorized query
            vector_query = VectorizedQuery(
                vector=query_vector, k_nearest_neighbors=k, fields="content_vector"
            )

            filter_parts = []
            if filter:
                # Convert dict filter to OData filter string
                # Example: {"source": "doc1"} -> "source eq 'doc1'"
                for key, value in filter.items():
                    if isinstance(value, str):
                        filter_parts.append(f"{key} eq '{value}'")
                    else:
                        filter_parts.append(f"{key} eq {value}")

            filter_expr = " and ".join(filter_parts) if filter_parts else None

            results = search_client.search(
                search_text=None,  # Pure vector search
                vector_queries=[vector_query],
                filter=filter_expr,
                top=k,
            )

            # Convert to LangChain Document format
            docs_with_scores = []
            async for result in results:
                metadata = json.loads(result.get("metadata", "{}"))
                doc = Document(page_content=result["content"], metadata=metadata)
                # Azure Search returns similarity score (higher is better)
                score = result["@search.score"]
                docs_with_scores.append((doc, score))

            return docs_with_scores

        except Exception:
            logger.exception(f"Failed to search Azure Search index '{collection}'")
            return []

    async def has_document(self, doc_id: str, collection_name: str | None = "default") -> bool:
        """
        Check if document exists in index.

        Args:
            doc_id: Document ID
            collection_name: Index name

        Returns:
            True if document exists
        """
        try:
            search_client = self._get_search_client(collection_name)
            result = await search_client.get_document(key=doc_id)
            return result is not None
        except ResourceNotFoundError:
            return False
        except Exception as e:
            logger.warning(f"Error checking document '{doc_id}' in index '{collection_name}': {e}")
            return False

    async def search_by_vector(
        self,
        query_vector: list[float],
        collection_name: str = "default",
        limit: int = 10,
    ) -> list[dict]:
        """
        Search by precomputed vector.

        Args:
            query_vector: Query embedding vector
            collection_name: Index name
            limit: Number of results to return

        Returns:
            List of result dictionaries with 'content' and 'metadata' keys
        """
        try:
            search_client = self._get_search_client(collection_name)

            # Create vectorized query
            vector_query = VectorizedQuery(
                vector=query_vector, k_nearest_neighbors=limit, fields="content_vector"
            )

            # Execute search
            results = search_client.search(
                search_text=None,  # Pure vector search
                vector_queries=[vector_query],
                filter=None,
                top=limit,
            )

            # Convert to dictionary format
            docs = []
            async for result in results:
                metadata = json.loads(result.get("metadata", "{}"))
                docs.append(
                    {
                        "content": result["content"],
                        "metadata": metadata,
                        "score": result["@search.score"],
                    }
                )

            return docs

        except Exception:
            logger.exception(
                f"Failed to search by vector in Azure Search index '{collection_name}'"
            )
            return []

    async def clear_collection(self, collection_name: str | None = None) -> bool:
        """
        Delete an entire index (mimics ChromaDB clear_collection).

        Args:
            collection_name: Index name to delete

        Returns:
            True if successful
        """
        try:
            index_name = self._get_index_name(collection_name or "default")
            await self.index_client.delete_index(index_name)

            # Remove from cache and close client
            if index_name in self._search_client_cache:
                await self._search_client_cache[index_name].close()
                del self._search_client_cache[index_name]

            logger.info(f"Deleted index '{index_name}'")
            return True

        except ResourceNotFoundError:
            logger.warning(f"Index '{collection_name}' does not exist")
            return False
        except Exception:
            logger.exception(f"Failed to delete index '{collection_name}'")
            return False

    async def get_documents(
        self, include: list[str] | None = None, collection_name: str = "default"
    ) -> list[Document]:
        """
        Retrieve all documents from an index.
        Warning: This can be expensive for large indexes.

        Args:
            include: Fields to include (not used in Azure Search)
            collection_name: Index name

        Returns:
            List of Documents
        """
        try:
            search_client = self._get_search_client(collection_name)

            # Retrieve all documents (no filter, no vector query)
            results = search_client.search(
                search_text="*",  # Match all
                select=["id", "content", "metadata"],
            )

            docs = []
            async for result in results:
                metadata = json.loads(result.get("metadata", "{}"))
                doc = Document(page_content=result["content"], metadata=metadata)
                docs.append(doc)

            return docs

        except Exception:
            logger.exception(f"Failed to retrieve documents from index '{collection_name}'")
            return []

    @property
    def score_mode(self) -> str:
        """Return score mode for compatibility."""
        return "similarity"  # Azure Search returns similarity scores (higher is better)
