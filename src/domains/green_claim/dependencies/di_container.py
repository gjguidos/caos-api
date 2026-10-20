from types import SimpleNamespace

from dependency_injector import containers, providers

from src.base.dependencies.di_container import Container as BaseContainer
from src.domains.agentverse.services.db_service import DBService
from src.domains.green_claim.services.green_claim_audit_service import (
    GreenClaimAuditService,
)
from src.domains.green_claim.services.green_claim_service import GreenClaimService


class GreenClaimContainer(containers.DeclarativeContainer):
    """
    Dependency injection container for the Green Claim domain.
    """

    openai_repository = providers.Dependency()
    mongodb_repository = providers.Dependency()

    # DB service is a singleton (no state)
    db_service = providers.Singleton(
        DBService,
        mongodb_repository=mongodb_repository,
    )

    # Audit service depends on DB service
    green_claim_audit_service = providers.Singleton(GreenClaimAuditService, db_service=db_service)

    # Green claim service depends on OpenAI repo and audit service
    green_claim_service = providers.Singleton(
        GreenClaimService,
        openai_repository=openai_repository,
        audit_service=green_claim_audit_service,
    )


def extend_container(base_container: BaseContainer) -> BaseContainer:
    """
    Extend the base container with Green Claim domain dependencies.

    Args:
        base_container: The base container to extend

    Returns:
        The extended container with Green Claim domain dependencies
    """
    gc = GreenClaimContainer()

    # Override the openai_repository dependency with the one from base_container
    gc.openai_repository.override(base_container.llm_container.default_repository)

    # Override the mongodb_repository dependency with the one from base_container
    gc.mongodb_repository.override(base_container.database.mongodb_repository)

    gc.wire(
        modules=[
            "src.domains.green_claim.dependencies.get_green_claim_service",
            "src.domains.green_claim.dependencies.get_green_claim_audit_service",
            "src.domains.green_claim.interfaces.api.v1.routes",
        ]
    )

    # Create the domain namespace on the base container
    if not hasattr(base_container, "green_claim"):
        base_container.green_claim = SimpleNamespace()

    ns = base_container.green_claim

    # Mount Green Claim providers under the namespace
    ns.green_claim_service = gc.green_claim_service
    ns.green_claim_audit_service = gc.green_claim_audit_service

    return base_container
