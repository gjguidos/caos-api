import logging

from dependency_injector.wiring import Provide, inject
from fastapi import Depends

from src.base.dependencies.request_id_dependency import get_request_id
from src.domains.agentverse.dependencies.di_container import AgentverseContainer
from src.domains.agentverse.services.personality_service import PersonalityService

logger = logging.getLogger(__name__)


@inject
async def get_personality_service(
    request_id: str = Depends(get_request_id),
    personality_service: PersonalityService = Depends(
        Provide[AgentverseContainer.personality_service]
    ),
):
    logger.info(f"creating PersonalityService with request_id: {request_id}")
    personality_service.request_id = request_id
    logger.info(f"PersonalityService created {personality_service}")
    return personality_service
