"""GET /api/v1/dev/files/{file_id} — local-dev file serving endpoint.

Active only when BLOB_STORAGE_LOCAL_PATH is set. Replaces SAS URL fetches that would
normally go to Azure Blob Storage, so the tool server can retrieve uploaded files
without any cloud dependency.

SECURITY: this endpoint is intentionally absent in production (guarded by config check).
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response

from src.base.config.config import settings
from src.base.services.file_service import FileMissingError, FileService
from src.base.services.local_file_service import LocalFileService
from src.domains.agentverse.dependencies.get_file_service import get_file_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Dev"])


@router.get("/api/v1/dev/files/{file_id}", include_in_schema=False)
async def serve_local_file(
    file_id: str,
    user_id: str = Query(..., description="Owning user ID"),
    exp: int = Query(..., description="Unix timestamp expiry"),
    file_service: FileService | None = Depends(get_file_service),
) -> Response:
    if not settings.blob_storage.blob_storage_local_path:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    if time.time() > exp:
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="URL expired")

    if not isinstance(file_service, LocalFileService):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Local file service not active",
        )

    try:
        data, mime = file_service.read_bytes(file_id, user_id)
    except FileMissingError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"File '{file_id}' not found",
        )

    logger.debug("Served local file file_id=%s mime=%s size=%d", file_id, mime, len(data))
    return Response(content=data, media_type=mime)
