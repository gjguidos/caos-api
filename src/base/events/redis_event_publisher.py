from __future__ import annotations

import logging

from src.base.events.channels import user_event_channel
from src.base.events.models import DomainEvent
from src.base.events.publisher import DomainEventPublisher
from src.base.repositories.redis_repository import RedisRepository

logger = logging.getLogger(__name__)


class RedisDomainEventPublisher(DomainEventPublisher):
    """
    Publish domain events through Redis Pub/Sub.
    """

    def __init__(
        self,
        *,
        redis_repository: RedisRepository,
        channel: str = "agentverse:domain-events",
    ) -> None:
        self._redis_repository = redis_repository
        self._channel = channel

    async def publish(
        self,
        event: DomainEvent,
    ) -> int:
        payload = event.model_dump(
            mode="json",
            exclude_none=True,
        )

        if event.user_id:
            channel = user_event_channel(str(event.user_id))
        else:
            channel = self._channel

        subscriber_count = await self._redis_repository.publish(
            channel,
            payload,
        )

        logger.debug(
            "Published domain event: type=%s source=%s channel=%s subscribers=%s",
            event.event_type,
            event.source,
            channel,
            subscriber_count,
        )

        return subscriber_count