from __future__ import annotations

from typing import Protocol

from src.base.operations.events import OperationEvent


class OperationEventPublisher(Protocol):
    async def publish(
        self,
        event: OperationEvent,
    ) -> None:
        ...