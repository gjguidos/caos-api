import logging
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile

from src.base.authentication.rbac import require_roles_and_scopes
from src.base.authentication.role import Role
from src.base.entities.user import User
from src.domains.green_claim.dependencies.get_green_claim_audit_service import (
    get_green_claim_audit_service,
)
from src.domains.green_claim.dependencies.get_green_claim_service import get_green_claim_service
from src.domains.green_claim.entities.audit_models import (
    AnalysisType,
    GreenClaimAudit,
    GreenClaimAuditFilter,
)
from src.domains.green_claim.entities.green_claim_models import (
    GreenClaimRequest,
    GreenClaimResponse,
)
from src.domains.green_claim.services.green_claim_audit_service import GreenClaimAuditService
from src.domains.green_claim.services.green_claim_service import GreenClaimService
from src.domains.green_claim.utils.file_utils import convert_upload_file_to_data_uri

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Green Claim"])


@router.post(
    "/api/v1/green-claim/analyze",
    response_model=GreenClaimResponse,
    summary="Analyze text for green claims",
    description="""Analyzes the provided text for potential greenwashing and identifies problematic environmental claims.
    
    Set include_formatted_report=true to include a human-readable text report in the response.
    
    For file analysis (images or PDFs), use the `/api/v1/green-claim/analyze-file` endpoint instead.
    """,
)
async def analyze_green_claim(
    request_body: GreenClaimRequest,
    green_claim_service: GreenClaimService = Depends(get_green_claim_service),
    user: User = Depends(
        require_roles_and_scopes(required_roles=[[Role.ADMIN], [Role.GREEN_CLAIM_USER]])
    ),
) -> GreenClaimResponse:
    """
    Analyze text for green claims and potential greenwashing.

    This endpoint uses AI to analyze the provided text for environmental claims
    that may be misleading or constitute greenwashing. It also identifies specific
    problematic words commonly associated with greenwashing.

    Args:
        request_body: The request containing the text to analyze
        green_claim_service: The injected GreenClaimService

    Returns:
        GreenClaimResponse: Analysis report and list of problematic words found

    Raises:
        HTTPException: If the analysis fails
    """
    logger.info(f"Received green claim analysis request - text_length={len(request_body.text)}")

    if request_body.chunk_text:
        result = await green_claim_service.run_chunked_green_claim_analysis(
            text=request_body.text,
            use_second_opinion=request_body.use_second_opinion,
            user_id=user.id,
            include_formatted_report=request_body.include_formatted_report,
        )
    else:
        result = await green_claim_service.run_green_claim_analysis(
            text=request_body.text,
            use_second_opinion=request_body.use_second_opinion,
            user_id=user.id,
            include_formatted_report=request_body.include_formatted_report,
        )

    logger.info("Green claim analysis successful")

    return result


@router.post(
    "/api/v1/green-claim/analyze-file",
    response_model=GreenClaimResponse,
    summary="Analyze image or PDF file for green claims",
    description="""Analyzes an uploaded image or PDF file for potential greenwashing and identifies problematic environmental claims.
    
    Supported file types:
    - Images: JPEG, PNG, WebP
    - Documents: PDF (including images within PDFs)
    
    Maximum file size: 10 MB
    
    Set include_formatted_report=true to include a human-readable text report in the response.
    
    Use the `/api/v1/green-claim/analyze` endpoint for text-only analysis.
    """,
)
async def analyze_green_claim_file(
    file: UploadFile = File(..., description="Image or PDF file to analyze"),
    use_second_opinion: bool = Form(
        default=False,
        description="Whether to run analysis twice and merge with confidence ratings",
    ),
    include_formatted_report: bool = Form(
        default=False,
        description="Whether to include a human-readable formatted report in the response",
    ),
    green_claim_service: GreenClaimService = Depends(get_green_claim_service),
    user: User = Depends(
        require_roles_and_scopes(required_roles=[[Role.ADMIN], [Role.GREEN_CLAIM_USER]])
    ),
) -> GreenClaimResponse:
    """
    Analyze an uploaded file (image or PDF) for green claims and potential greenwashing.

    This endpoint accepts file uploads via multipart/form-data and uses AI to analyze
    the file content for environmental claims that may be misleading or constitute
    greenwashing. For PDFs, the analysis includes any images contained within the document.

    Args:
        file: The uploaded image or PDF file
        use_second_opinion: Whether to run analysis twice and merge results
        green_claim_service: The injected GreenClaimService

    Returns:
        GreenClaimResponse: Analysis report (problematic_words will be empty for file uploads)

    Raises:
        HTTPException: If file validation fails or analysis fails
    """
    logger.info(
        f"Received file upload for green claim analysis - "
        f"filename={file.filename}, content_type={file.content_type}, "
        f"use_second_opinion={use_second_opinion}"
    )

    # Convert uploaded file to base64 data URI with validation
    file_data, mime_type = await convert_upload_file_to_data_uri(file)

    logger.info(f"File converted successfully - mime_type={mime_type}")

    # Run file analysis
    result = await green_claim_service.run_green_claim_file_analysis(
        file_data=file_data,
        mime_type=mime_type,
        filename=file.filename,
        use_second_opinion=use_second_opinion,
        user_id=user.id,
        include_formatted_report=include_formatted_report,
    )

    logger.info("Green claim file analysis successful")
    return result


@router.get(
    "/api/v1/green-claim/audits",
    response_model=list[GreenClaimAudit],
    summary="List green claim analysis audit logs",
    description="""Query audit logs of green claim analyses with filtering options.
    
    Supports filtering by:
    - user_object_id: Filter by specific user
    - analysis_type: Filter by analysis type (text, chunked_text, file)
    - success: Filter by success/failure status
    - start_date/end_date: Filter by date range
    
    Includes pagination with limit and skip parameters.
    """,
)
async def list_green_claim_audits(
    user_object_id: str | None = Query(None, description="Filter by user ID"),
    analysis_type: AnalysisType | None = Query(None, description="Filter by analysis type"),
    success: bool | None = Query(None, description="Filter by success status"),
    start_date: datetime | None = Query(None, description="Filter by start date (ISO 8601)"),
    end_date: datetime | None = Query(None, description="Filter by end date (ISO 8601)"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of results"),
    skip: int = Query(0, ge=0, description="Number of results to skip"),
    audit_service: GreenClaimAuditService = Depends(get_green_claim_audit_service),
    user: User = Depends(require_roles_and_scopes(required_roles=[[Role.ADMIN]])),
):
    """
    List green claim analysis audit logs with filtering and pagination.

    Admin-only endpoint for compliance and usage tracking.

    Args:
        user_object_id: Optional user ID filter
        analysis_type: Optional analysis type filter
        success: Optional success status filter
        start_date: Optional start date filter
        end_date: Optional end date filter
        limit: Maximum number of results
        skip: Number of results to skip
        user: Authenticated admin user

    Returns:
        List of GreenClaimAudit entities

    Raises:
        HTTPException: If query fails
    """
    filters = GreenClaimAuditFilter(
        user_object_id=user_object_id,
        analysis_type=analysis_type,
        success=success,
        start_date=start_date,
        end_date=end_date,
    )

    logger.info(f"Querying audit logs with filters: {filters.model_dump()}")

    audits = await audit_service.list_audits(filters=filters, limit=limit, skip=skip)

    logger.info(f"Retrieved {len(audits)} audit logs")

    return audits


@router.get(
    "/api/v1/green-claim/audits/{audit_id}",
    response_model=GreenClaimAudit,
    summary="Get specific audit log by ID",
    description="Retrieve a single green claim analysis audit log by its ID.",
)
async def get_green_claim_audit(
    audit_id: str,
    audit_service: GreenClaimAuditService = Depends(get_green_claim_audit_service),
    user: User = Depends(require_roles_and_scopes(required_roles=[[Role.ADMIN]])),
):
    """
    Get a specific audit log by ID.

    Admin-only endpoint.

    Args:
        audit_id: The audit log ID
        user: Authenticated admin user

    Returns:
        GreenClaimAudit entity

    Raises:
        HTTPException: If audit not found or query fails
    """
    audit = await audit_service.get_audit_by_id(audit_id)

    if not audit:
        raise HTTPException(
            status_code=404,
            detail=f"Audit log with ID {audit_id} not found",
        )

    return audit
