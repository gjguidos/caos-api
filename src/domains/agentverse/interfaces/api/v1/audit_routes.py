import logging

from fastapi import APIRouter, Depends, HTTPException

from src.base.authentication.rbac import require_roles_and_scopes
from src.base.authentication.role import Role
from src.base.entities.user import User
from src.domains.agentverse.dependencies.get_audit_service import (
    get_audit_service,
)
from src.domains.agentverse.entities.report_log import (
    OperationLog,
    OperationType,
)
from src.domains.agentverse.services.audit_service import AuditService

logger = logging.getLogger("agentverse.audit_routes")
router = APIRouter()


# ========================== OPERATION LOGS ==========================


@router.get("/api/v1/operations/{log_id}", tags=["Operations"], response_model=OperationLog)
async def get_operation_log(
    log_id: str,
    audit_service: AuditService = Depends(get_audit_service),
    user: User = Depends(require_roles_and_scopes(required_roles=[[Role.ADMIN]])),
):
    """
    Retrieve a single operation log by its ID.

    Args:
        log_id: The unique identifier of the operation log

    Returns:
        The operation log if found

    Raises:
        404: If the operation log is not found
    """
    log = await audit_service.get_operation_log(log_id)

    if not log:
        raise HTTPException(status_code=404, detail=f"Operation log with ID '{log_id}' not found")

    return log


@router.get("/api/v1/operations", tags=["Operations"], response_model=list[OperationLog])
async def list_operation_logs(
    audit_service: AuditService = Depends(get_audit_service),
    user: User = Depends(require_roles_and_scopes(required_roles=[[Role.ADMIN]])),
    operation_type: OperationType | None = None,
    user_object_id: str | None = None,
    limit: int = 100,
    skip: int = 0,
):
    """
    List operation logs with optional filtering and pagination.

    Query Parameters:
        operation_type: Filter by operation type (agent_create, agent_update, agent_delete, agent_read)
        user_object_id: Filter by user who performed the operation
        limit: Maximum number of logs to return (default: 100)
        skip: Number of logs to skip for pagination (default: 0)

    Returns:
        List of operation logs matching the filters
    """
    logs = await audit_service.list_operation_logs(
        operation_type=operation_type,
        user_object_id=user_object_id,
        limit=limit,
        skip=skip,
    )

    return logs
