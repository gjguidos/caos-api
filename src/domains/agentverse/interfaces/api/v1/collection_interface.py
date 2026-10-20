import base64
import json
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from src.base.authentication.rbac import require_roles_and_scopes
from src.base.authentication.role import Role
from src.base.entities.user import User
from src.domains.agentverse.dependencies.get_db_service import get_db_service
from src.domains.agentverse.dependencies.get_pdf_ingestion_orchestration_service import (
    get_pdf_ingestion_orchestration_service,
)
from src.domains.agentverse.entities.collection import CollectionCreateMetadata
from src.domains.agentverse.exceptions import PDFIngestionError
from src.domains.agentverse.services.pdf_ingestion_orchestration_service import (
    PDFIngestionOrchestrator,
)

router = APIRouter(tags=["Agent Collections"])
logger = logging.getLogger("agentverse.interface")


@router.post(
    "/api/v1/agents/{agent_id}/collection/{collection_name}/ingest-pdf",
    status_code=status.HTTP_201_CREATED,
    summary="Ingest a PDF into an agent’s vector-DB collection or link existing collection to agent",
)
async def ingest_document(
    agent_id: str,
    collection_name: str,
    request: Request,
    file: UploadFile | None = File(None),
    collection_metadata: str = Form(...),
    orchestrator: PDFIngestionOrchestrator = Depends(get_pdf_ingestion_orchestration_service),
    user: User = Depends(require_roles_and_scopes(required_roles=[[Role.ADMIN]])),
):
    logger.info("[INGESTING PDF] Ingestion initiated")

    # 1. Validate file type (if provided)
    if file is not None and file.content_type != "application/pdf":
        raise HTTPException(status_code=415, detail="Only PDF uploads allowed")

    # 2. Parse metadata
    try:
        metadata_dict = json.loads(collection_metadata)
        collection_metadata_model = CollectionCreateMetadata(**metadata_dict)
    except (json.JSONDecodeError, ValidationError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid collection metadata: {str(e)}")

    # 3. Convert file to base64 if provided
    pdf_base64 = None
    original_filename = None
    if file is not None:
        try:
            file_bytes = await file.read()
            pdf_base64 = base64.b64encode(file_bytes).decode("utf-8")
            original_filename = file.filename
            await file.close()
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Failed to read uploaded file: {str(e)}")
    else:
        original_filename = collection_metadata_model.original_filename

    # 4. Call orchestrator
    try:
        result = await orchestrator.ingest_pdf(
            request=request,
            agent_id=agent_id,
            user_id=user.id,
            collection_name=collection_name,
            pdf_base64=pdf_base64,
            metadata=collection_metadata_model,
            original_filename=original_filename if file is not None else None,
        )
        return JSONResponse(status_code=201, content=result)

    except PDFIngestionError as e:
        logger.warning(f"PDF ingestion error: {e}")
        raise HTTPException(status_code=400, detail=str(e))

    except Exception:
        logger.exception("Unexpected error during ingestion")
        raise HTTPException(status_code=500, detail="Internal ingestion error")


@router.get("/api/v1/agents/collections")
async def list_collections(
    db_service=Depends(get_db_service),
    user: User = Depends(require_roles_and_scopes(required_roles=[[Role.USER]])),
):
    """
    List all collections across all agents.
    """
    try:
        # This should call a service that retrieves all collections
        # collections = await get_pdf_ingestion_orchestration_service().list_all_collections()
        collections = await db_service.find_all(collection_name="coll_vdb_collections")
        # return JSONResponse(status_code=200, content={"collections": collections})
        return collections
    except Exception as e:
        logger.exception(f"{e}")
        raise HTTPException(
            status_code=500, detail="Internal server error while listing collections"
        )


@router.get("/api/v1/agents/collections")
async def list_user_collections(
    db_service=Depends(get_db_service),
    user: User = Depends(require_roles_and_scopes(required_roles=[[Role.USER]])),
):
    """
    List all user owned or public collections.
    """
    try:
        logger.info(f"Listing collections for user_id={user.id}")
        collections = await db_service.find_collections(user.id)
        return collections
    except Exception as e:
        logger.exception(f"{e}")
        raise HTTPException(
            status_code=500, detail="Internal server error while listing collections"
        )
