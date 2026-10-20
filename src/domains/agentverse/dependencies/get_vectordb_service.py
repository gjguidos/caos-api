import logging

from dependency_injector.wiring import Provide, inject
from fastapi import Depends

from src.base.dependencies.request_id_dependency import get_request_id
from src.domains.agentverse.dependencies.di_container import AgentverseContainer
from src.domains.agentverse.services.vectordb_service import VectorDBService

logger = logging.getLogger(__name__)


@inject
async def get_vectordb_service(
    request_id: str = Depends(get_request_id),
    vectordb_service: VectorDBService = Depends(Provide[AgentverseContainer.vectordb_service]),
):
    logger.info(f"creating VectorDBService with request_id: {request_id}")
    vectordb_service.request_id = request_id
    logger.info(f"VectorDBService created {vectordb_service}")
    return vectordb_service
