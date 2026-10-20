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


class OperationsContainer(containers.DeclarativeContainer):
    """
    Container for generic operation and job infrastructure.
    """

    event_publisher = providers.Dependency()

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