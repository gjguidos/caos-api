import logging

from dependency_injector.wiring import Provide, inject
from fastapi import Depends

from src.base.dependencies.request_id_dependency import get_request_id
from src.domains.agentverse.dependencies.di_container import AgentverseContainer
from src.domains.agentverse.services.agent_chat_service import AgentChatService

logger = logging.getLogger(__name__)


@inject
async def get_agent_chat_service(
    request_id: str = Depends(get_request_id),
    agent_chat_service: AgentChatService = Depends(Provide[AgentverseContainer.agent_chat_service]),
):
    logger.info(f"creating AgentChatService with request_id: {request_id}")
    agent_chat_service.request_id = request_id
    logger.info(f"AgentChatService created {agent_chat_service}")
    return agent_chat_service
