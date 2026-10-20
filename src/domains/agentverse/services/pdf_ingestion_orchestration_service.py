import logging
from datetime import UTC, datetime

from src.domains.agentverse.entities.collection import CollectionCreateMetadata, CollectionDocument
from src.domains.agentverse.exceptions import PDFIngestionError
from src.domains.agentverse.services.db_service import DBService
from src.domains.agentverse.services.pdf_ingestion_service import PDFIngestionService
from src.domains.agentverse.services.vectordb_service import VectorDBService

logger = logging.getLogger(__name__)


class PDFIngestionOrchestrator:
    def __init__(
        self,
        pdf_ingestion_service: PDFIngestionService,
        db_service: DBService,
        vectordb_service: VectorDBService,
    ):
        self.pdf_service = pdf_ingestion_service
        self.db_service = db_service
        self.vectordb_service = vectordb_service

    async def ingest_pdf(
        self,
        request,
        *,
        agent_id: str,
        user_id: str,
        collection_name: str,
        pdf_base64: str | None,
        metadata: CollectionCreateMetadata | None = None,
        original_filename: str | None = None,
    ) -> dict:
        logger.info("Starting ingestion flow")

        agent = await self.db_service.find_by_id(agent_id=agent_id)
        if not agent:
            raise PDFIngestionError(f"Agent '{agent_id}' not found")

        col_data = await self.db_service.find_collection(
            agent_id=agent_id, collection_name=collection_name
        )
        is_new_collection = col_data is None

        if is_new_collection and not metadata:
            raise PDFIngestionError(
                f"Collection '{collection_name}' not found and no metadata provided"
            )

        default_embedding = (request.app.state.cognitive_defaults or {}).get("embedding")
        agent_embedding = agent.get("embedding_type") or default_embedding

        if is_new_collection:
            col = CollectionDocument(
                name=collection_name,
                agent_id=agent_id,
                owner_id=user_id,
                visibility=metadata.visibility,
                allowed=[],
                prompt_template=metadata.prompt_template or "",
                chunk_size=metadata.chunk_size,
                chunk_overlap=metadata.chunk_overlap,
                vector_metric=metadata.vector_metric,
                embedding_model=agent_embedding,
                updated_at=datetime.now(UTC),
            )
        else:
            col = CollectionDocument.model_validate(col_data)
            if col.visibility == "private" and col.owner_id != user_id:
                raise PDFIngestionError("Unauthorized to access private collection")
            if col.visibility == "shared" and user_id not in col.allowed:
                raise PDFIngestionError("Not allowed to access shared collection")
            if (col.embedding_model or default_embedding) != agent_embedding:
                raise PDFIngestionError(
                    f"Collection '{collection_name}' was embedded with "
                    f"'{col.embedding_model or default_embedding}', but this agent uses "
                    f"'{agent_embedding}'. Mixing embedding models in one collection makes "
                    f"its documents unretrievable."
                )

        if pdf_base64:
            try:
                extraction = self.pdf_service.extract_text_and_chunks(
                    pdf_base64=pdf_base64,
                    user_id=user_id,
                    filename=(
                        original_filename
                        or (getattr(metadata, "original_filename", None) if metadata else None)
                    ),
                )
            except ValueError as e:
                raise PDFIngestionError(str(e))

            chunks = extraction["chunks"]
            doc_hash = extraction["metadata"]["hash"]

            docs = []
            for i, chunk in enumerate(chunks):
                display = chunk.get("source_display") or chunk.get("filename") or "Unknown Document"
                docs.append(
                    {
                        "id": f"{user_id}_{collection_name}_{doc_hash}_{i}",
                        "content": chunk["content"],
                        "metadata": {
                            # ---- citation/display fields ----
                            "source": display,
                            "book_title": display,
                            "page": chunk.get("page"),
                            # ---- useful extras for debugging/fallbacks ----
                            "filename": chunk.get("filename"),
                            "book_machine_name": chunk.get("source"),
                            "book_id": chunk.get("source_id"),
                            "page_char_start": chunk.get("page_char_start"),
                            "page_char_end": chunk.get("page_char_end"),
                            "chunk_index": chunk.get("chunk_index", i),
                            # ---- ACL / routing ----
                            "user_id": user_id,
                            "agent_id": agent_id,
                            "collection": collection_name,
                            "doc_hash": doc_hash,
                        },
                    }
                )

            result = await self.vectordb_service.upsert_if_missing(
                request=request,
                collection=collection_name,
                documents=docs,
                embedding_key=col.embedding_model,
            )

            if is_new_collection:
                await self.db_service.upsert_collection(col)
            elif (
                metadata
                and col.owner_id == user_id
                and any(
                    [
                        metadata.chunk_size is not None,
                        metadata.chunk_overlap is not None,
                        metadata.vector_metric is not None,
                        metadata.prompt_template is not None,
                    ]
                )
            ):
                col = col.copy(
                    update={
                        "chunk_size": metadata.chunk_size or col.chunk_size,
                        "chunk_overlap": metadata.chunk_overlap or col.chunk_overlap,
                        "vector_metric": metadata.vector_metric or col.vector_metric,
                        "prompt_template": metadata.prompt_template or col.prompt_template or "",
                        "updated_at": datetime.now(UTC),
                    }
                )
                await self.db_service.upsert_collection(col)

            summary = {
                "status": result["status"],
                "chunks_total": len(chunks),
                "chunks_ingested": result.get("added", 0),
                "skipped": result.get("skipped", 0),
                "collection": collection_name,
                "doc_hash": doc_hash,
                **extraction["metadata"],
            }

        else:
            if is_new_collection:
                await self.db_service.upsert_collection(col)
            elif (
                metadata
                and col.owner_id == user_id
                and any(
                    [
                        metadata.chunk_size is not None,
                        metadata.chunk_overlap is not None,
                        metadata.vector_metric is not None,
                        metadata.prompt_template is not None,
                    ]
                )
            ):
                col = col.copy(
                    update={
                        "chunk_size": metadata.chunk_size or col.chunk_size,
                        "chunk_overlap": metadata.chunk_overlap or col.chunk_overlap,
                        "vector_metric": metadata.vector_metric or col.vector_metric,
                        "prompt_template": metadata.prompt_template or col.prompt_template or "",
                        "updated_at": datetime.now(UTC),
                    }
                )
                await self.db_service.upsert_collection(col)

            summary = {
                "status": "linked",
                "chunks_total": 0,
                "chunks_ingested": 0,
                "skipped": 0,
                "collection": collection_name,
            }

        await self.db_service.add_collection_to_agent(
            agent_id=agent_id,
            collection_name=collection_name,
        )

        return summary
