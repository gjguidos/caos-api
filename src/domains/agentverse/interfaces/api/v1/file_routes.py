"""POST /api/v1/files — Upload a file bound to a specific MCP tool or workflow."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from fastapi import Form as FormField
from pydantic import BaseModel

from src.base.authentication.permission import Permission
from src.base.authentication.rbac import require_roles_and_scopes
from src.base.authentication.role import Role
from src.base.entities.user import User
from src.base.services.file_service import FileService
from src.domains.agentverse.dependencies.get_file_service import get_file_service
from src.domains.agentverse.dependencies.get_workflow_service import get_workflow_service
from src.domains.agentverse.exceptions import WorkflowNotFoundError
from src.domains.agentverse.logging.logger_v1 import AGENTVERSE_LOGGER, LogDomain
from src.domains.agentverse.registries.registries import tool_registry_instance
from src.domains.agentverse.services.workflow_service import WorkflowService
from src.domains.agentverse.tools.spec import FilesSupport
from src.domains.agentverse.utils.canonicalize import canonicalize

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Files"])

# Workflows declare no per-input size field, so workflow-target uploads get one
# shared cap (still bounded by the service-level GLOBAL_MAX_BYTES).
WORKFLOW_UPLOAD_MAX_BYTES = 50 * 1024 * 1024


class FileUploadResponse(BaseModel):
    file_id: str
    name: str
    mime: str
    size: int
    sha256: str


def _resolve_files_support(target_tool: str) -> tuple[str, FilesSupport]:
    """
    Resolve target_tool to a canonical key and its FilesSupport config.

    Raises HTTPException 400 if the tool does not exist or does not support files.
    """
    key = canonicalize(target_tool or "")
    if key not in tool_registry_instance:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"target_tool '{target_tool}' not found in registry",
        )

    meta = tool_registry_instance.get_metadata_for_ref(key)
    supports = meta.get("supports") or {}
    files_raw = supports.get("files")

    if not files_raw:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Tool '{key}' does not support file uploads",
        )

    fs = FilesSupport(**files_raw) if isinstance(files_raw, dict) else FilesSupport()
    return key, fs


async def _resolve_workflow_constraints(
    *,
    workflow_service: WorkflowService,
    target_workflow: str,
    target_input: str | None,
) -> tuple[str, list[str] | None]:
    """
    Resolve target_workflow (+ optional target_input) to (canonical key, accepted mimes).

    Without target_input any mime is accepted (per-input mimes are re-checked at
    run start against the actual input the file lands in). With target_input the
    upload is validated precisely against that declared file input.

    Raises HTTPException 400 when the workflow or input does not resolve.
    """
    try:
        doc = await workflow_service.get_workflow(target_workflow)
    except WorkflowNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"target_workflow '{target_workflow}' not found",
        )

    if target_input is None:
        return doc.key, None

    field = next((f for f in doc.inputs if f.key == target_input), None)
    if field is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Workflow '{doc.key}' declares no input '{target_input}'",
        )
    if not field.is_file:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Workflow '{doc.key}' input '{target_input}' is not a file input",
        )

    return doc.key, field.accepted_mimes or None


@dataclass(frozen=True)
class _UploadTarget:
    """The canonical destination an upload is bound to, plus its size/mime
    constraints. Exactly one of ``tool_key``/``workflow_key`` is non-empty."""

    tool_key: str
    workflow_key: str
    accepted_mimes: list[str] | None
    max_size_bytes: int


async def _resolve_upload_target(
    *,
    target_tool: str | None,
    target_workflow: str | None,
    target_input: str | None,
    workflow_service: WorkflowService,
) -> _UploadTarget:
    """Resolve exactly one of ``target_tool``/``target_workflow`` to the upload's
    canonical target and constraints. Raises HTTPException 400 when the target is
    missing, ambiguous, or does not resolve."""
    if bool(target_tool) == bool(target_workflow):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide exactly one of target_tool or target_workflow",
        )

    if target_tool:
        tool_key, files_support = _resolve_files_support(target_tool)
        return _UploadTarget(
            tool_key=tool_key,
            workflow_key="",
            accepted_mimes=files_support.accepted_mimes,
            max_size_bytes=files_support.max_size_bytes,
        )

    workflow_key, accepted_mimes = await _resolve_workflow_constraints(
        workflow_service=workflow_service,
        target_workflow=target_workflow or "",
        target_input=target_input,
    )
    return _UploadTarget(
        tool_key="",
        workflow_key=workflow_key,
        accepted_mimes=accepted_mimes,
        max_size_bytes=WORKFLOW_UPLOAD_MAX_BYTES,
    )


@router.post(
    "/api/v1/files", response_model=FileUploadResponse, status_code=status.HTTP_201_CREATED
)
async def upload_file(
    file: UploadFile,
    target_tool: str | None = FormField(
        default=None, description="Target tool key (e.g. 'extract')"
    ),
    target_workflow: str | None = FormField(
        default=None, description="Target workflow key (mutually exclusive with target_tool)"
    ),
    target_input: str | None = FormField(
        default=None,
        description="Declared file-input key of target_workflow to validate mimes against",
    ),
    agent_id: str | None = FormField(default=None, description="Reserved, not enforced in v1"),
    user: User = Depends(
        require_roles_and_scopes(
            required_roles=[[Role.USER]], required_permissions=[Permission.AGENT_WRITE]
        )
    ),
    file_service: FileService | None = Depends(get_file_service),
    workflow_service: WorkflowService = Depends(get_workflow_service),
):
    if file_service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="File storage is not configured on this server",
        )

    AGENTVERSE_LOGGER.info(
        LogDomain.COMMAND_ROOM,
        "[🌐 API][📎 FILES] upload_start",
        extra={
            "user_id": user.id,
            "target_tool": target_tool,
            "target_workflow": target_workflow,
            "target_input": target_input,
            "filename": file.filename,
        },
    )

    # Fail-fast: resolve the target and its constraints before reading the body.
    target = await _resolve_upload_target(
        target_tool=target_tool,
        target_workflow=target_workflow,
        target_input=target_input,
        workflow_service=workflow_service,
    )

    data = await file.read()

    try:
        metadata = await file_service.upload(
            user_id=user.id or "",
            tenant_id="default",
            target_tool=target.tool_key,
            target_workflow=target.workflow_key,
            filename=file.filename or "",
            data=data,
            accepted_mimes=target.accepted_mimes,
            max_size_bytes=target.max_size_bytes,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    AGENTVERSE_LOGGER.info(
        LogDomain.COMMAND_ROOM,
        "[🌐 API][📎 FILES] upload_success",
        extra={
            "user_id": user.id,
            "file_id": metadata.file_id,
            "name": metadata.name,
            "size": metadata.size,
            "sha256": metadata.sha256,
            "target_tool": target.tool_key,
            "target_workflow": target.workflow_key,
        },
    )

    return FileUploadResponse(
        file_id=metadata.file_id,
        name=metadata.name,
        mime=metadata.mime,
        size=metadata.size,
        sha256=metadata.sha256,
    )
