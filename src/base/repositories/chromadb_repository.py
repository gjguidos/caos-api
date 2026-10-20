import logging
from typing import Any

import anyio
from langchain_core.documents import Document

from src.base.infrastructure.db.chromaDB.chromadb_client import ChromaDBClient

logger = logging.getLogger(__name__)


class ChromaDBRepository:
    def __init__(self, chromadb_client: ChromaDBClient):
        self.chromadb_client = chromadb_client

    def add_documents(self, new_chunks: list[Document], ids: list[str]) -> bool:
        """
        Add documents to the Chroma vector store and persist changes.

        Args:
            documents (List[Document]): List of Document objects to be added.
            ids (List[str]): Corresponding list of unique identifiers for the documents.

        Returns:
            bool: True if the documents were added and persisted successfully; False otherwise.
        """
        try:
            return self.chromadb_client.add_documents(documents=new_chunks, ids=ids)
        except Exception as e:
            logger.error(e)

    async def search_with_score(
        self,
        *,
        request: Any | None = None,
        collection: str,
        query: str,
        top_k: int = 10,
        metadata_filter: dict | None = None,
        query_vector: list[float] | None = None,
    ) -> list[tuple[Document, float]]:
        vs = self.chromadb_client.get_vectorstore(collection)  # uses the cache
        logger.info(
            "[ChromaDBRepository] search_with_score collection='%s' k=%d filter=%s vector=%s",
            collection,
            top_k,
            metadata_filter,
            query_vector is not None,
        )

        # A caller-supplied vector was produced by the collection's own embedding
        # backend, which may not be the one bound to this vector store.
        if query_vector is not None:

            def _call_by_vector():
                return vs.similarity_search_by_vector_with_relevance_scores(
                    query_vector, k=top_k, filter=metadata_filter
                )

            return await anyio.to_thread.run_sync(_call_by_vector)

        # Prefer async if available (supported by the VectorStore interface)
        if hasattr(vs, "asimilarity_search_with_score"):
            return await vs.asimilarity_search_with_score(
                query, k=top_k, filter=metadata_filter
            )  # :contentReference[oaicite:3]{index=3}

        # Fallback to sync in a worker thread
        def _call():
            return vs.similarity_search_with_score(
                query, k=top_k, filter=metadata_filter
            )  # :contentReference[oaicite:4]{index=4}

        return await anyio.to_thread.run_sync(_call)

    async def add_embeddings(
        self,
        embeddings: list[list[float]],
        ids: list[str],
        metadatas: list[dict],
        contents: list[str],  # ✅ Add this
        collection_name: str,
    ) -> bool:
        try:
            return await anyio.to_thread.run_sync(
                self.chromadb_client.add_embeddings,
                embeddings,
                ids,
                metadatas,
                contents,
                collection_name,
            )
        except Exception as e:
            logger.error(e)

    async def search(
        self, query: str, collection: str, top_k: int = 5, request: Any | None = None
    ) -> list[Document]:
        try:
            return self.chromadb_client.query([query], n_results=top_k, collection_name=collection)
        except Exception:
            logger.exception(f"[ChromaDBRepository] ❌ Failed to search collection '{collection}'")
            return []

    def get_documents(self, include: list[str] | None = None) -> list[Document]:
        """
        Retrieve documents from the Chroma vector store.

        Args:
            include (Optional[List[str]]): List of fields to include (e.g., metadata).

        Returns:
            List[Document]: A list of Document objects retrieved from the store.
        """
        try:
            return self.chromadb_client.get_documents(include)
        except Exception as e:
            logger.error(e)

    def clear_collection(self, collection_name: str | None = None) -> bool:
        """
        Clear the entire collection (or a specific collection if applicable) in the vector store.

        In an enterprise setup, this action should be used with caution as it resets persisted data.

        Args:
            collection_name (Optional[str]): The name of the collection to clear. Defaults to the primary collection if None.

        Returns:
            bool: True if the collection was cleared successfully; False otherwise.
        """
        try:
            return self.chromadb_client.clear_collection(collection_name)
        except Exception as e:
            logger.error(e)

    def has_document(self, doc_id: str, collection_name: str = "default") -> bool:
        """
        Check if a document exists without requiring the client to expose a helper.
        Tries vectorstore._collection, falls back to client.get_collection.
        """
        try:
            # 1) Try to use the underlying Chroma collection from the LangChain vectorstore
            vs = self.chromadb_client.get_vectorstore(collection_name)
            coll = getattr(vs, "_collection", None)

            # 2) Fallback: try to grab the collection from the client directly
            if coll is None:
                get_coll = getattr(self.chromadb_client, "get_collection", None)
                if callable(get_coll):
                    coll = get_coll(collection_name)

            if coll is None:
                logger.warning("Chroma collection handle not available for has_document()")
                return False

            res = coll.get(ids=[doc_id], include=[])
            ids = res.get("ids", [])
            if isinstance(ids, list) and ids and isinstance(ids[0], list):
                ids = ids[0]
            return bool(ids)

        except Exception as e:
            logger.error(f"Error checking document existence in Chroma: {e}")
            return False

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
            List of result dictionaries with 'content' and 'metadata' keys
        """
        return await anyio.to_thread.run_sync(
            self.chromadb_client.search_by_vector,
            query_vector,
            collection_name,
            limit,
        )
