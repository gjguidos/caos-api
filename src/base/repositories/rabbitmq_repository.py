from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aio_pika import ExchangeType
from aio_pika.abc import AbstractIncomingMessage

from src.base.infrastructure.messaging.rabbitmq.rabbitmq_client import RabbitMQClient

logger = logging.getLogger(__name__)

MessageHandler = Callable[[AbstractIncomingMessage], Awaitable[None]]
MessagePayload = dict[str, Any] | str | bytes


class RabbitMQRepository:
    """Async transport repository; event policy belongs to CAOS EventRuntimeV2."""

    def __init__(self, rabbitmq_client: RabbitMQClient) -> None:
        self.rabbitmq_client = rabbitmq_client

    async def connect(self) -> None:
        await self.rabbitmq_client.connect()

    async def declare_queue(
        self,
        queue_name: str,
        *,
        durable: bool = True,
        arguments: dict[str, Any] | None = None,
    ) -> None:
        await self.rabbitmq_client.declare_queue(
            queue_name, durable=durable, arguments=arguments
        )

    async def declare_exchange(
        self,
        exchange_name: str,
        *,
        exchange_type: ExchangeType = ExchangeType.TOPIC,
        durable: bool = True,
    ) -> None:
        await self.rabbitmq_client.declare_exchange(
            exchange_name, exchange_type=exchange_type, durable=durable
        )

    async def bind_queue(
        self, queue_name: str, exchange_name: str, routing_key: str
    ) -> None:
        await self.rabbitmq_client.bind_queue(queue_name, exchange_name, routing_key)

    async def publish_message(
        self,
        queue_name: str,
        message: MessagePayload,
        *,
        message_id: str | None = None,
        correlation_id: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> None:
        try:
            await self.rabbitmq_client.basic_publish(
                queue_name=queue_name,
                message=message,
                message_id=message_id,
                correlation_id=correlation_id,
                headers=headers,
            )
            logger.debug("Published to RabbitMQ queue=%s", queue_name)
        except Exception:
            logger.exception("RabbitMQ publish failed queue=%s", queue_name)
            raise

    async def publish_event(
        self,
        exchange_name: str,
        routing_key: str,
        message: MessagePayload,
        *,
        message_id: str | None = None,
        correlation_id: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> None:
        try:
            await self.rabbitmq_client.publish_event(
                exchange_name=exchange_name,
                routing_key=routing_key,
                message=message,
                message_id=message_id,
                correlation_id=correlation_id,
                headers=headers,
            )
            logger.debug(
                "Published RabbitMQ event exchange=%s routing_key=%s",
                exchange_name,
                routing_key,
            )
        except Exception:
            logger.exception(
                "RabbitMQ event publish failed exchange=%s routing_key=%s",
                exchange_name,
                routing_key,
            )
            raise

    async def consume_queue(
        self,
        queue_name: str,
        on_message_callback: MessageHandler,
        *,
        requeue_on_error: bool = False,
    ) -> str:
        """Register a consumer and return its registration ID.

        Callback receives aio-pika IncomingMessage and must be async.
        The client owns message acknowledgments.
        """
        try:
            registration_id = await self.rabbitmq_client.consume_messages(
                queue_name=queue_name,
                on_message_callback=on_message_callback,
                requeue_on_error=requeue_on_error,
            )
            logger.info(
                "Registered RabbitMQ consumer queue=%s registration=%s",
                queue_name,
                registration_id,
            )
            return registration_id
        except Exception:
            logger.exception("RabbitMQ consumer registration failed queue=%s", queue_name)
            raise

    async def stop_consuming(self, registration_id: str | None = None) -> None:
        await self.rabbitmq_client.stop_consuming(registration_id)

    async def purge_queue(self, queue_name: str) -> None:
        await self.rabbitmq_client.purge_queue(queue_name)
        logger.info("Purged RabbitMQ queue=%s", queue_name)

    async def delete_queue(self, queue_name: str) -> None:
        await self.rabbitmq_client.delete_queue(queue_name)
        logger.info("Deleted RabbitMQ queue=%s", queue_name)

    async def close_connection(self) -> None:
        await self.rabbitmq_client.stop()
        logger.info("RabbitMQ connection closed")
