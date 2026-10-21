from __future__ import annotations

from dependency_injector.wiring import Provide, inject
from fastapi import Depends

from src.base.dependencies.di_container import Container
from src.base.services.version_service import VersionService


@inject
def get_version_service(
    version_service: VersionService = Depends(
        Provide[
            Container.services.version_service
        ]
    ),
) -> VersionService:
    return version_service