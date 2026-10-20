import logging

from dependency_injector.wiring import Provide, inject
from fastapi import Depends

from src.base.dependencies.request_id_dependency import get_request_id
from src.domains.agentverse.dependencies.di_container import AgentverseContainer
from src.domains.agentverse.services.audit_service import AuditService

logger = logging.getLogger(__name__)


@inject
async def get_audit_service(
    request_id: str = Depends(get_request_id),
    audit_service: AuditService = Depends(Provide[AgentverseContainer.audit_service]),
):
    logger.info(f"creating AuditService with request_id: {request_id}")
    audit_service.request_id = request_id
    logger.info(f"AuditService created {audit_service}")
    return audit_service
