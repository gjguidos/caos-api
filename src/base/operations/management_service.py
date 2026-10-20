from __future__ import annotations

from src.base.operations.models import (
    OperationRecord,
    OperationStatus,
)
from src.base.operations.runner import (
    OperationAlreadyFinishedError,
    OperationNotFoundError,
    OperationNotRunningError,
    OperationRunner,
)
from src.base.operations.service import OperationService


class OperationForbiddenError(PermissionError):
    def __init__(self, run_id: str) -> None:
        super().__init__(
            f"User is not allowed to access operation '{run_id}'."
        )


class OperationManagementService:
    """
    Application-facing service for querying and controlling operations.

    This service coordinates OperationService and OperationRunner.
    It does not perform background supervision or cleanup.
    """

    def __init__(
        self,
        *,
        operation_service: OperationService,
        operation_runner: OperationRunner,
    ) -> None:
        self.operation_service = operation_service
        self.operation_runner = operation_runner

    async def get_status(
        self,
        *,
        run_id: str,
        user_id: str,
    ) -> OperationRecord:
        operation = await self.operation_service.get(run_id)

        if operation is None:
            raise OperationNotFoundError(
                f"Operation '{run_id}' was not found."
            )

        self._authorize(
            operation=operation,
            user_id=user_id,
        )

        return operation

    async def cancel(
        self,
        *,
        run_id: str,
        user_id: str,
    ) -> OperationRecord:
        operation = await self.get_status(
            run_id=run_id,
            user_id=user_id,
        )

        await self.operation_runner.cancel(
            run_id=operation.run_id,
        )

        updated = await self.operation_service.get(
            operation.run_id
        )

        return updated or operation

    async def list_operations(
        self,
        *,
        user_id: str,
        status: OperationStatus | None = None,
        limit: int = 50,
    ) -> list[OperationRecord]:
        if not user_id:
            raise ValueError(
                "user_id must not be empty."
            )

        if limit <= 0:
            raise ValueError(
                "limit must be greater than zero."
            )

        if limit > 100:
            raise ValueError(
                "limit must not be greater than 100."
            )

        return await self.operation_service.list_operations(
            user_id=user_id,
            status=status,
            limit=limit,
        )

    @staticmethod
    def _authorize(
        *,
        operation: OperationRecord,
        user_id: str,
    ) -> None:
        if operation.user_id != user_id:
            raise OperationForbiddenError(
                operation.run_id
            )