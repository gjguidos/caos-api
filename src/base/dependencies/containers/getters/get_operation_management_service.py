from fastapi import Request

from src.base.operations.management_service import (
    OperationManagementService,
)


def get_operation_management_service(
    request: Request,
) -> OperationManagementService:
    return request.app.container.operations.operation_management_service()