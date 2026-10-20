import logging

from dependency_injector.wiring import Provide, inject
from fastapi import Depends

from src.base.dependencies.request_id_dependency import get_request_id
from src.domains.agentverse.dependencies.di_container import AgentverseContainer
from src.domains.agentverse.services.db_service import DBService

logger = logging.getLogger(__name__)


@inject
async def get_db_service(
    request_id: str = Depends(get_request_id),
    db_service: DBService = Depends(Provide[AgentverseContainer.db_service]),
):
    logger.info(f"creating DBService with request_id: {request_id}")
    db_service.request_id = request_id
    logger.info(f"DBService created {db_service}")
    return db_service
