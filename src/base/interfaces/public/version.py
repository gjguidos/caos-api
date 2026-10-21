from dataclasses import asdict

from fastapi import APIRouter, Depends

from src.base.dependencies.version_dependencies import (
    get_version_service,
)
from src.base.services.version_service import VersionService

router = APIRouter(
    tags=["System"],
)


@router.get("/version")
async def get_application_version(
    version_service: VersionService = Depends(
        get_version_service
    ),
):
    return asdict(version_service.get_version())