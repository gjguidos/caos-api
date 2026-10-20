from dependency_injector.wiring import Provide, inject
from fastapi import Depends

from src.base.dependencies.request_id_dependency import get_request_id
from src.domains.agentverse.dependencies.di_container import AgentverseContainer
from src.domains.agentverse.services.workflow_service import WorkflowService


@inject
async def get_workflow_service(
    request_id: str = Depends(get_request_id),
    workflow_service: WorkflowService = Depends(Provide[AgentverseContainer.workflow_service]),
) -> WorkflowService:
    return workflow_service
