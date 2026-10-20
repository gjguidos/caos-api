import logging

from dependency_injector.wiring import Provide, inject
from fastapi import Depends

from src.domains.agentverse.dependencies.di_container import AgentverseContainer
from src.domains.agentverse.operations.service import OperationService

logger = logging.getLogger(__name__)


@inject
async def get_operation_service(
    operation_service: OperationService = Depends(
        Provide[AgentverseContainer.operation_service]
    ),
) -> OperationService:
    return operation_service