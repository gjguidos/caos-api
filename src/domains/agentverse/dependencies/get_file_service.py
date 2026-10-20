import logging

from dependency_injector.wiring import Provide, inject
from fastapi import Depends

from src.base.dependencies.request_id_dependency import get_request_id
from src.base.services.file_service import FileService
from src.domains.agentverse.dependencies.di_container import AgentverseContainer

logger = logging.getLogger(__name__)


@inject
async def get_file_service(
    request_id: str = Depends(get_request_id),
    file_service: FileService = Depends(Provide[AgentverseContainer.file_service]),
) -> FileService | None:
    return file_service
