"""
REST CRUD for the persisted workflow catalog (``coll_workflows``).

Auto-discovered by ``src/base/routing/auto_routing.py`` (exposes a top-level
``router``). Reads use UTU catalog policy; create/edit/delete are
ADMIN, enforced in-app (no external ``workflow.*`` permissions).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status

from src.base.authentication.rbac import require_roles_and_scopes
from src.base.authentication.role import Role
from src.base.entities.user import User
from src.domains.agentverse.dependencies.get_workflow_service import get_workflow_service
from src.domains.agentverse.entities.workflow import (
    WorkflowCreateRequest,
    WorkflowDocument,
    WorkflowUpdateRequest,
    WorkflowValidationResult,
)
from src.domains.agentverse.logging.logger_v1 import AGENTVERSE_LOGGER, LogDomain
from src.domains.agentverse.services.workflow_service import WorkflowService

router = APIRouter(tags=["Workflows"])


@router.get("/api/v1/workflows/list")
async def list_workflows(
    workflow_service: WorkflowService = Depends(get_workflow_service),
    user: User = Depends(require_roles_and_scopes(required_roles=[[Role.USER]])),
) -> list[WorkflowDocument]:
    AGENTVERSE_LOGGER.info(
        LogDomain.COMMAND_ROOM,
        "[🌐 API][🧭 WORKFLOWS] list_start",
        extra={"user_id": getattr(user, "id", None)},
    )
    return await workflow_service.list_workflows(user)


@router.get("/api/v1/workflows/key/{key}")
async def get_workflow(
    key: str,
    workflow_service: WorkflowService = Depends(get_workflow_service),
    user: User = Depends(require_roles_and_scopes(required_roles=[[Role.USER]])),
) -> WorkflowDocument:
    AGENTVERSE_LOGGER.info(
        LogDomain.COMMAND_ROOM,
        "[🌐 API][🧭 WORKFLOWS] get_start",
        extra={"user_id": getattr(user, "id", None), "workflow_key": key},
    )
    return await workflow_service.get_workflow(key, user)


@router.post("/api/v1/workflows", status_code=status.HTTP_201_CREATED)
async def create_workflow(
    request: WorkflowCreateRequest,
    workflow_service: WorkflowService = Depends(get_workflow_service),
    user: User = Depends(require_roles_and_scopes(required_roles=[[Role.ADMIN]])),
) -> WorkflowDocument:
    AGENTVERSE_LOGGER.info(
        LogDomain.COMMAND_ROOM,
        "[🌐 API][🧭 WORKFLOWS] create_start",
        extra={"user_id": getattr(user, "id", None), "name": request.name},
    )
    return await workflow_service.create_workflow(request, user)


@router.patch("/api/v1/workflows/key/{key}")
async def update_workflow(
    key: str,
    request: WorkflowUpdateRequest,
    workflow_service: WorkflowService = Depends(get_workflow_service),
    user: User = Depends(require_roles_and_scopes(required_roles=[[Role.ADMIN]])),
) -> WorkflowDocument:
    AGENTVERSE_LOGGER.info(
        LogDomain.COMMAND_ROOM,
        "[🌐 API][🧭 WORKFLOWS] update_start",
        extra={"user_id": getattr(user, "id", None), "workflow_key": key},
    )
    return await workflow_service.update_workflow(key, request, user)


@router.delete("/api/v1/workflows/key/{key}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow(
    key: str,
    workflow_service: WorkflowService = Depends(get_workflow_service),
    user: User = Depends(require_roles_and_scopes(required_roles=[[Role.ADMIN]])),
) -> Response:
    AGENTVERSE_LOGGER.info(
        LogDomain.COMMAND_ROOM,
        "[🌐 API][🧭 WORKFLOWS] delete_start",
        extra={"user_id": getattr(user, "id", None), "workflow_key": key},
    )
    await workflow_service.delete_workflow(key, user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/api/v1/workflows/validate")
async def validate_workflow(
    request: WorkflowCreateRequest,
    workflow_service: WorkflowService = Depends(get_workflow_service),
    user: User = Depends(require_roles_and_scopes(required_roles=[[Role.ADMIN]])),
) -> WorkflowValidationResult:
    """Validate a definition without persisting it."""
    AGENTVERSE_LOGGER.info(
        LogDomain.COMMAND_ROOM,
        "[🌐 API][🧭 WORKFLOWS] validate_start",
        extra={"user_id": getattr(user, "id", None)},
    )
    return workflow_service.validate_workflow(
        request.definition,
        inputs=request.inputs,
    )
