import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aio_pika import IncomingMessage

from src.base.repositories.rabbitmq_repository import RabbitMQRepository

logger = logging.getLogger(__name__)

MessageHandler = Callable[[IncomingMessage], Awaitable[None]]


class RabbitMQService:
    """Application-facing RabbitMQ messaging service."""

    def __init__(self, repository: RabbitMQRepository):
        self.repository = repository

    async def publish(
        self,
        queue_name: str,
        message: dict[str, Any],
    ) -> None:
        await self.repository.publish_message(
            queue_name=queue_name,
            message=message,
        )

    async def publish_event(
        self,
        exchange_name: str,
        routing_key: str,
        message: dict[str, Any],
        correlation_id: str | None = None,
        *,
        message_id: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> None:
        await self.repository.publish_event(
            exchange_name=exchange_name,
            routing_key=routing_key,
            message=message,
            correlation_id=correlation_id,
            message_id=message_id,
            headers=headers,
        )

    async def setup_queue(self, queue_name: str) -> None:
        await self.repository.declare_queue(queue_name)

    async def setup_event_queue(
        self,
        exchange_name: str,
        queue_name: str,
        routing_key: str,
    ) -> None:
        await self.repository.declare_exchange(exchange_name)
        await self.repository.declare_queue(queue_name)

        await self.repository.bind_queue(
            queue_name=queue_name,
            exchange_name=exchange_name,
            routing_key=routing_key,
        )

    async def subscribe(
        self,
        queue_name: str,
        handler: MessageHandler,
    ) -> str:
        return await self.repository.consume_queue(
            queue_name=queue_name,
            on_message_callback=handler,
        )

    async def stop_consuming(self, registration_id: str | None = None) -> None:
        await self.repository.stop_consuming(registration_id)

    async def stop(self) -> None:
        await self.repository.close_connection()
        logger.info("RabbitMQ service stopped")
