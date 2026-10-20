import logging

from dependency_injector.wiring import Provide, inject
from fastapi import Depends

from src.base.dependencies.request_id_dependency import get_request_id
from src.domains.agentverse.dependencies.di_container import AgentverseContainer
from src.domains.agentverse.services.pdf_ingestion_orchestration_service import (
    PDFIngestionOrchestrator,
)

logger = logging.getLogger(__name__)


@inject
async def get_pdf_ingestion_orchestration_service(
    request_id: str = Depends(get_request_id),
    pdf_ingestion_orchestration_service: PDFIngestionOrchestrator = Depends(
        Provide[AgentverseContainer.pdf_ingestion_orchestrator_service]
    ),
):
    logger.info(f"creating PDFIngestionOrchestrator with request_id: {request_id}")
    pdf_ingestion_orchestration_service.request_id = request_id
    logger.info(f"PDFIngestionOrchestrator created {pdf_ingestion_orchestration_service}")
    return pdf_ingestion_orchestration_service
