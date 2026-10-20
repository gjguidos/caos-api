from __future__ import annotations

import asyncio
from typing import Protocol

from src.base.operations.models import (
    OperationProgress,
    OperationRecord,
    OperationStatus,
)


class OperationRepository(Protocol):
    async def create(
        self,
        operation: OperationRecord,
    ) -> None:
        ...

    async def list(
        self,
        *,
        user_id: str | None = None,
        status: OperationStatus | None = None,
        limit: int = 50,
    ) -> list[OperationRecord]:
        ...

    async def get(
        self,
        run_id: str,
    ) -> OperationRecord | None:
        ...

    async def update(
        self,
        operation: OperationRecord,
    ) -> None:
        ...

    async def update_progress(
        self,
        run_id: str,
        *,
        progress: OperationProgress,
    ) -> OperationRecord:
        ...

class InMemoryOperationRepository:
    def __init__(self) -> None:
        self._operations: dict[str, OperationRecord] = {}
        self._lock = asyncio.Lock()

    async def create(
        self,
        operation: OperationRecord,
    ) -> None:
        async with self._lock:
            self._operations[operation.run_id] = operation.model_copy(
                deep=True
            )

    async def list(
        self,
        *,
        user_id: str | None = None,
        status: OperationStatus | None = None,
        limit: int = 50,
    ) -> list[OperationRecord]:
        if limit <= 0:
            raise ValueError(
                "limit must be greater than zero."
            )

        async with self._lock:
            operations = [
                operation
                for operation in self._operations.values()
                if (
                    user_id is None
                    or operation.user_id == user_id
                )
                and (
                    status is None
                    or operation.status == status
                )
            ]

            operations.sort(
                key=lambda operation: operation.created_at,
                reverse=True,
            )

            return [
                operation.model_copy(deep=True)
                for operation in operations[:limit]
            ]

    async def get(
        self,
        run_id: str,
    ) -> OperationRecord | None:
        async with self._lock:
            operation = self._operations.get(run_id)

            if operation is None:
                return None

            return operation.model_copy(deep=True)

    async def update(
        self,
        operation: OperationRecord,
    ) -> None:
        async with self._lock:
            self._operations[operation.run_id] = operation.model_copy(
                deep=True
            )

    async def update_progress(
        self,
        run_id: str,
        *,
        progress: OperationProgress,
    ) -> OperationRecord:
        async with self._lock:
            operation = self._operations.get(run_id)

            if operation is None:
                raise KeyError(
                    f"Operation '{run_id}' not found."
                )

            if operation.progress == progress:
                return operation.model_copy(deep=True)

            operation.progress = progress
            operation.progress_revision += 1

            return operation.model_copy(deep=True)