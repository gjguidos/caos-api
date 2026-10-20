import logging
from collections.abc import Callable
from typing import Any

from langchain_chroma import Chroma
from langchain_core.documents import Document

from src.base.config.utils.utils import resolve_path

logger = logging.getLogger(__name__)


class ChromaDBClient:
    """
    Embedding-agnostic interface for Chroma vector store with support for named collections.

    Notes
    -----
    * With modern `langchain-chroma` + Chroma >= 0.4, there is **no `.persist()` method** on the
      LangChain `Chroma` vectorstore object. Persistence is automatic when `persist_directory`
      is provided. Do NOT call `.persist()` on the vectorstore.  # See API docs / issues.
    """

    def __init__(
        self,
        embedding_function: Callable,  # LangChain Embeddings object/function
        client=None,  # Optional low-level chromadb.Client
        persist_directory: str | None = None,
    ):
        self.client = client
        self.embedding_function = embedding_function
        self.chroma_path = resolve_path("CHROMA_PATH", "chroma_db")
        self.persist_directory = persist_directory or self.chroma_path

        self._vs_cache: dict[str, Chroma] = {}
        logger.info("ChromaDBClient initialized; persist dir: %s", self.persist_directory)

    # ---------- helpers ----------

    def get_vectorstore(self, collection_name: str) -> Chroma:
        """
        Return (and cache) a LangChain Chroma vectorstore for this collection.
        Persistence is handled automatically by Chroma when `persist_directory` is set.
        """
        if collection_name in self._vs_cache:
            return self._vs_cache[collection_name]

        if self.client is not None:
            vs = Chroma(
                client=self.client,
                collection_name=collection_name,
                embedding_function=self.embedding_function,
            )
        else:
            vs = Chroma(
                collection_name=collection_name,
                persist_directory=self.persist_directory,
                embedding_function=self.embedding_function,
            )
        self._vs_cache[collection_name] = vs
        return vs

    def _get_raw_collection(self, collection_name: str):
        """
        Access the underlying chromadb Collection (for advanced operations like
        adding **precomputed embeddings**).
        """
        vs = self.get_vectorstore(collection_name)
        coll = getattr(vs, "_collection", None)
        if coll is None:
            raise RuntimeError("Unable to access underlying Chroma collection handle.")
        return coll

    # ---------- ingest paths ----------

    def add_documents(
        self,
        documents: list[Document],
        ids: list[str],
        collection_name: str | None = "default",
    ) -> bool:
        """
        Add (and embed via embedding_function) Documents to the collection.

        No explicit persist call is needed; Chroma persists automatically when configured
        with `persist_directory`.
        """
        try:
            vs = self.get_vectorstore(collection_name)
            vs.add_documents(documents=documents, ids=ids)
            logger.info("Added %d documents to collection '%s'", len(documents), collection_name)
            return True
        except Exception:
            logger.exception("Failed to add documents to Chroma collection '%s'.", collection_name)
            return False

    def add_embeddings(
        self,
        embeddings: list[list[float]],
        ids: list[str],
        metadatas: list[dict],
        contents: list[str],
        collection_name: str | None = "default",
    ) -> bool:
        """
        Add precomputed embeddings directly to the underlying Chroma collection.
        This bypasses the embedding_function (no re-embedding).

        Parameters
        ----------
        embeddings : List[List[float]]
            Vectors aligned 1:1 with ids/contents/metadatas
        ids : List[str]
        metadatas : List[dict]
        contents : List[str]
            Raw texts for each vector (stored as documents)
        """
        try:
            if not (len(embeddings) == len(ids) == len(metadatas) == len(contents)):
                raise ValueError("Input arrays must all be the same length.")

            coll = self._get_raw_collection(collection_name)
            # Direct chromadb Collection API: accepts `embeddings=...`
            coll.add(
                ids=ids,
                embeddings=embeddings,
                documents=contents,
                metadatas=metadatas,
            )
            logger.info("Added %d precomputed embeddings to '%s'", len(ids), collection_name)
            return True
        except Exception:
            logger.exception(
                "Failed to add precomputed embeddings to collection '%s'", collection_name
            )
            return False

    # ---------- queries & maintenance ----------

    def query(
        self,
        query_texts: list[str],
        n_results: int,
        collection_name: str = "default",
    ) -> list[Document]:
        """
        Simple similarity query that returns Documents (no scores).
        """
        try:
            vs = self.get_vectorstore(collection_name)
            results = vs.similarity_search(query_texts[0], k=n_results)
            return results
        except Exception:
            logger.exception("Failed to query collection '%s'", collection_name)
            return []

    async def similarity_search_with_score(
        self,
        query: str,
        collection: str,
        k: int = 5,
        filter: dict | None = None,
        request: Any | None = None,
    ):
        """
        Async/sync-agnostic path: use sync method here; your repo may wrap this in a thread if needed.
        """
        vs = self.get_vectorstore(collection)
        return vs.similarity_search_with_score(query, k=k, filter=filter)

    def get_documents(
        self, include: list[str] | None = None, collection_name: str = "default"
    ) -> list[Document]:
        """
        Retrieve documents; this uses the vectorstore interface.
        """
        try:
            vs = self.get_vectorstore(collection_name)
            docs = vs.get(include=include or ["metadatas", "documents"])
            # Convert the raw dict to LangChain Documents (optional; keep as needed)
            out: list[Document] = []
            for doc_text, meta, _id in zip(
                docs.get("documents") or [],
                docs.get("metadatas") or [],
                docs.get("ids") or [],
            ):
                if doc_text is None and meta is None:
                    continue
                out.append(Document(page_content=doc_text or "", metadata=meta or {}, id=_id))
            return out
        except Exception:
            logger.exception("Failed to get documents from Chroma.")
            return []

    def clear_collection(self, collection_name: str | None = None) -> bool:
        """
        Drop the named collection.
        """
        try:
            vs = self.get_vectorstore(collection_name or "default")
            vs.delete_collection()
            # Cache cleanup
            self._vs_cache.pop(collection_name or "default", None)
            logger.info("Cleared Chroma collection '%s'", collection_name or "default")
            return True
        except Exception:
            logger.exception(
                "Failed to clear Chroma collection '%s'.", collection_name or "default"
            )
            return False

    def has_document(self, doc_id: str, collection_name: str | None = "default") -> bool:
        """
        Check if a document exists by ID.

        Uses the vectorstore's `get`/`get_by_ids` methods (available in current langchain-chroma).
        """
        try:
            vs = self.get_vectorstore(collection_name or "default")
            # Prefer get_by_ids (returns List[Document]) if available
            if hasattr(vs, "get_by_ids"):
                docs = vs.get_by_ids([doc_id])
                return bool(docs)

            # Fallback to raw get
            data = vs.get(ids=[doc_id], include=["metadatas"])
            ids = data.get("ids") or []
            return doc_id in ids
        except Exception as e:
            logger.error("Error checking document existence in Chroma: %s", e)
            return False

    def search_by_vector(
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
            coll = self._get_raw_collection(collection_name)

            # Query using precomputed embeddings
            results = coll.query(
                query_embeddings=[query_vector],
                n_results=limit,
                include=["documents", "metadatas", "distances"],
            )

            # Convert to dictionary format
            docs = []
            documents = results.get("documents", [[]])[0]
            metadatas = results.get("metadatas", [[]])[0]
            distances = results.get("distances", [[]])[0]

            for content, metadata, distance in zip(documents, metadatas, distances):
                docs.append(
                    {
                        "content": content,
                        "metadata": metadata or {},
                        "distance": distance,
                    }
                )

            return docs

        except Exception as e:
            logger.exception(f"Failed to search by vector in Chroma collection '{collection_name}'")
            return []
