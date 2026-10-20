from __future__ import annotations

from abc import ABC, abstractmethod

from src.base.events.models import DomainEvent


class DomainEventPublisher(ABC):
    """
    Transport-independent contract for publishing domain events.
    """

    @abstractmethod
    async def publish(
        self,
        event: DomainEvent,
    ) -> int:
        """
        Publish an event and return the number of immediate subscribers.
        """

        raise NotImplementedError