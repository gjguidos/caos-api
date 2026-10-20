import logging

from dependency_injector.wiring import Provide, inject
from fastapi import Depends

from src.domains.agentverse.dependencies.di_container import AgentverseContainer
from src.domains.agentverse.repositories.run_report_repository import RunReportRepository

logger = logging.getLogger(__name__)


@inject
async def get_run_report_repository(
    repository: RunReportRepository = Depends(Provide[AgentverseContainer.run_report_repository]),
) -> RunReportRepository:
    return repository
