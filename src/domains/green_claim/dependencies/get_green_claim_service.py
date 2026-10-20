import logging

from dependency_injector.wiring import Provide, inject
from fastapi import Depends

from src.domains.green_claim.dependencies.di_container import GreenClaimContainer
from src.domains.green_claim.services.green_claim_service import GreenClaimService

logger = logging.getLogger(__name__)


@inject
async def get_green_claim_service(
    green_claim_service: GreenClaimService = Depends(
        Provide[GreenClaimContainer.green_claim_service]
    ),
):
    return green_claim_service
