from dependency_injector import containers, providers

from src.base.operations.job_supervisor import JobSupervisor
from src.base.operations.management_service import (
    OperationManagementService,
)
from src.base.operations.repository import (
    InMemoryOperationRepository,
)
from src.base.operations.runner import OperationRunner
from src.base.operations.service import OperationService
from src.base.operations.v2.mongo_managed_repository import MongoManagedOperationRepository
from src.base.operations.v2.mongo_ownership_repository import MongoOwnershipRepository
from src.base.operations.v2.mongo_repository import MongoOperationRepository


class OperationsContainer(containers.DeclarativeContainer):
    """
    Container for generic operation and job infrastructure.
    """

    event_publisher = providers.Dependency()
    mongodb_repository = providers.Dependency()

    operation_repository_v2 = providers.Singleton(
        MongoOperationRepository,
        mongodb_repository=mongodb_repository,
    )

    ownership_repository_v2 = providers.Singleton(
        MongoOwnershipRepository,
        mongodb_repository=mongodb_repository,
    )

    managed_operation_repository_v2 = providers.Singleton(
        MongoManagedOperationRepository,
        mongodb_repository=mongodb_repository,
    )

    operation_repository = providers.Singleton(
        InMemoryOperationRepository,
    )

    operation_service = providers.Singleton(
        OperationService,
        repository=operation_repository,
        event_publisher=event_publisher,
    )

    operation_runner = providers.Singleton(
        OperationRunner,
        service=operation_service,
    )

    operation_management_service = providers.Singleton(
        OperationManagementService,
        operation_service=operation_service,
        operation_runner=operation_runner,
    )

    job_supervisor = providers.Singleton(
        JobSupervisor,
        operation_service=operation_service,
    )
