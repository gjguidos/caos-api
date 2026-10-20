from dependency_injector.wiring import Provide, inject
from fastapi import Depends

from src.base.dependencies.request_id_dependency import get_request_id
from src.domains.agentverse.dependencies.di_container import AgentverseContainer
from src.domains.agentverse.services.agent_service import AgentService


@inject
async def get_agent_service(
    request_id: str = Depends(get_request_id),
    agent_service: AgentService = Depends(Provide[AgentverseContainer.agent_service]),
):

    agent_service.request_id = request_id

    return agent_service
