from dependency_injector.wiring import Provide, inject
from fastapi import Depends

from src.base.dependencies.request_id_dependency import get_request_id
from src.domains.agentverse.dependencies.di_container import AgentverseContainer
from src.domains.agentverse.services.registry_service import RegistryService


@inject
async def get_registry_service(
    request_id: str = Depends(get_request_id),
    registry_service: RegistryService = Depends(Provide[AgentverseContainer.registry_service]),
):
    registry_service.request_id = request_id
    return registry_service
