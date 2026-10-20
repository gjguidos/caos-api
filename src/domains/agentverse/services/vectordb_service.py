import logging
from typing import Any

from langchain_core.documents import Document

from src.domains.agentverse.services.db_service import DBService

logger = logging.getLogger(__name__)


class VectorDBService:
    """Owns the pairing of a collection with the embedding backend its vectors use.

    Documents can only be retrieved with the same embedding model they were written
    with, so both sides of that contract live here rather than at the call sites.
    """

    def __init__(self, db_service: DBService) -> None:
        self.db_service = db_service

    @staticmethod
    def _resolve_repos(request: Any, embedding_key: str | None) -> tuple[Any, Any]:
        """Locate the knowledge_db and embedding backends for this operation.

        A None ``embedding_key`` means the global default; an unknown one is an
        error, because silently substituting a different model would search a
        vector space the documents were never written into.
        """
        cm = request.app.state.cognitive_modules
        defaults = request.app.state.cognitive_defaults

        kb_key = defaults.get("knowledge_db")
        embedding_key = embedding_key or defaults.get("embedding")

        if not kb_key or kb_key not in cm.get("knowledge_db", {}):
            raise ValueError("❌ No valid knowledge_db backend configured.")
        if not embedding_key or embedding_key not in cm.get("embedding", {}):
            raise ValueError(f"❌ No valid embedding backend configured: {embedding_key!r}")

        return cm["knowledge_db"][kb_key], cm["embedding"][embedding_key]

    async def embedding_key_for_collection(self, collection: str) -> str | None:
        """The embedding backend ``collection`` was written with, None for the default."""
        col = await self.db_service.find_collection_by_name(collection)
        return col.embedding_model if col else None

    async def upsert_if_missing(
        self,
        request: Any,
        collection: str,
        documents: list[dict[str, Any]],
        embedding_key: str | None = None,
    ) -> dict:
        vector_repo, embedding_repo = self._resolve_repos(request, embedding_key)

        new_ids: list[str] = []
        texts_to_embed: list[str] = []
        metadatas: list[dict[str, Any]] = []

        for doc in documents:
            doc_id = doc.get("id")
            content = doc.get("content")

            # Skip if the id or content is not a valid, non-empty string
            if not isinstance(doc_id, str) or not doc_id:
                logger.warning("Skipping document without an id")
                continue
            if not isinstance(content, str) or not content.strip():
                logger.warning(f"Skipping invalid or empty content for doc id={doc_id}")
                continue

            if not vector_repo.has_document(doc_id, collection_name=collection):
                new_ids.append(doc_id)
                texts_to_embed.append(content)
                metadatas.append(doc.get("metadata") or {})

        if not new_ids:
            return {
                "status": "skipped",
                "reason": "All documents already exist",
                "added": 0,
                "skipped": len(documents),
            }

        # Generate embeddings only for new documents
        embeddings = await embedding_repo.get_embeddings_async(texts_to_embed)

        # Optimized insert using embeddings
        success = await vector_repo.add_embeddings(
            embeddings=embeddings,
            ids=new_ids,
            metadatas=metadatas,
            contents=texts_to_embed,
            collection_name=collection,
        )

        return {
            "status": "upserted" if success else "failed",
            "added": len(new_ids),
            "skipped": len(documents) - len(new_ids),
        }

    async def search_with_score(
        self,
        request: Any,
        collection: str,
        query: str,
        top_k: int = 10,
        metadata_filter: dict | None = None,
        embedding_key: str | None = None,
    ) -> list[tuple[Document, float]]:
        """Similarity search that embeds ``query`` with the collection's own backend."""
        vector_repo, embedding_repo = self._resolve_repos(request, embedding_key)

        embeddings = await embedding_repo.get_embeddings_async([query])

        return await vector_repo.search_with_score(
            request=request,
            collection=collection,
            query=query,
            top_k=top_k,
            metadata_filter=metadata_filter,
            query_vector=embeddings[0],
        )
