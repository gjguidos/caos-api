"""Concurrency-safe reference adapter; deliberately not durable."""

import asyncio
from uuid import UUID

from src.base.operations.v2.models import OperationRecord
from src.base.operations.v2.repository import (
    DuplicateOperation,
    OperationNotFound,
    RevisionConflict,
    validate_revision,
    validate_snapshot,
    validate_successor,
)


class InMemoryOperationRepository:
    def __init__(self) -> None:
        self._operations: dict[UUID, OperationRecord] = {}
        self._lock = asyncio.Lock()

    async def create(self, operation: OperationRecord) -> None:
        operation = validate_snapshot(operation)
        async with self._lock:
            if operation.operation_id in self._operations:
                raise DuplicateOperation(operation.operation_id)
            self._operations[operation.operation_id] = operation

    async def get(self, operation_id: UUID) -> OperationRecord:
        async with self._lock:
            if operation_id not in self._operations:
                raise OperationNotFound(operation_id)
            return validate_snapshot(self._operations[operation_id])

    async def commit(self, operation: OperationRecord, *, expected_revision: int) -> None:
        operation = validate_snapshot(operation)
        validate_revision(operation, expected_revision)
        async with self._lock:
            previous = self._operations.get(operation.operation_id)
            if previous is None:
                raise OperationNotFound(operation.operation_id)
            if previous.revision != expected_revision:
                raise RevisionConflict(operation.operation_id)
            validate_successor(previous, operation)
            self._operations[operation.operation_id] = operation
