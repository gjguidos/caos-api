from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

import aio_pika
from aio_pika import DeliveryMode, ExchangeType, Message
from aio_pika.abc import (
    AbstractIncomingMessage,
    AbstractRobustChannel,
    AbstractRobustConnection,
    AbstractRobustExchange,
    AbstractRobustQueue,
)

logger = logging.getLogger(__name__)

MessageHandler = Callable[[AbstractIncomingMessage], Awaitable[None]]


class RabbitMQClient:
    """Async AMQP transport for CAOS, independent of event authorization."""

    def __init__(
        self,
        url: str,
        *,
        prefetch_count: int = 10,
        timeout: float = 10.0,
    ) -> None:
        self.url = url
        self.prefetch_count = prefetch_count
        self.timeout = timeout

        self.connection: AbstractRobustConnection | None = None
        self.publish_channel: AbstractRobustChannel | None = None
        self.consumer_channel: AbstractRobustChannel | None = None

        self._consumers: dict[str, tuple[AbstractRobustQueue, str]] = {}
        self._lock = asyncio.Lock()
        self._closing = False

    async def connect(self) -> None:
        """Establish one robust connection with independent AMQP channels."""
        async with self._lock:
            if self._closing:
                raise RuntimeError("RabbitMQ client is shutting down")
            if (
                self.connection is not None
                and not self.connection.is_closed
                and self.publish_channel is not None
                and not self.publish_channel.is_closed
                and self.consumer_channel is not None
                and not self.consumer_channel.is_closed
            ):
                await self.publish_channel.ready()
                await self.consumer_channel.ready()
                return

            # Do not overwrite handles left by an interrupted connection attempt.
            cleanup_errors = await self._close_resources()
            if cleanup_errors:
                raise cleanup_errors[0]
            try:
                self.connection = await aio_pika.connect_robust(
                    self.url,
                    timeout=self.timeout,
                )
                self.publish_channel = await self.connection.channel(
                    publisher_confirms=True,
                    on_return_raises=True,
                )
                self.consumer_channel = await self.connection.channel()
                await self.consumer_channel.set_qos(prefetch_count=self.prefetch_count)
            except BaseException:
                # Rollback must not replace the original connection/cancellation error.
                await self._close_resources()
                raise
            logger.info("RabbitMQ client connected")

    async def _ensure_connected(self) -> None:
        if self._closing:
            raise RuntimeError("RabbitMQ client is shutting down")
        if (
            self.connection is None
            or self.connection.is_closed
            or self.publish_channel is None
            or self.publish_channel.is_closed
            or self.consumer_channel is None
            or self.consumer_channel.is_closed
        ):
            await self.connect()
        else:
            await self.publish_channel.ready()
            await self.consumer_channel.ready()

    async def declare_queue(
        self,
        queue_name: str,
        *,
        durable: bool = True,
        arguments: dict[str, Any] | None = None,
    ) -> AbstractRobustQueue:
        await self._ensure_connected()
        assert self.consumer_channel is not None
        return await self.consumer_channel.declare_queue(
            queue_name,
            durable=durable,
            arguments=arguments,
        )

    async def declare_exchange(
        self,
        exchange_name: str,
        *,
        exchange_type: ExchangeType = ExchangeType.TOPIC,
        durable: bool = True,
    ) -> AbstractRobustExchange:
        await self._ensure_connected()
        assert self.publish_channel is not None
        return await self.publish_channel.declare_exchange(
            exchange_name,
            type=exchange_type,
            durable=durable,
        )

    async def bind_queue(
        self,
        queue_name: str,
        exchange_name: str,
        routing_key: str,
    ) -> None:
        queue = await self.declare_queue(queue_name)
        # Declare on the same channel as the queue, so the channel-scoped
        # exchange object can be used to bind it.
        assert self.consumer_channel is not None
        exchange = await self.consumer_channel.declare_exchange(
            exchange_name,
            type=ExchangeType.TOPIC,
            durable=True,
        )
        await queue.bind(exchange, routing_key=routing_key)

    async def basic_publish(
        self,
        queue_name: str,
        message: dict[str, Any] | str | bytes,
        *,
        message_id: str | None = None,
        correlation_id: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> None:
        """Publish to a queue through RabbitMQ's default exchange."""
        await self.declare_queue(queue_name)
        await self.publish_event(
            exchange_name="",
            routing_key=queue_name,
            message=message,
            message_id=message_id,
            correlation_id=correlation_id,
            headers=headers,
        )

    async def publish_event(
        self,
        exchange_name: str,
        routing_key: str,
        message: dict[str, Any] | str | bytes,
        *,
        message_id: str | None = None,
        correlation_id: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> None:
        """Publish with persistence and publisher confirmations."""
        await self._ensure_connected()
        assert self.publish_channel is not None
        if isinstance(message, dict):
            body = json.dumps(message).encode("utf-8")
            content_type = "application/json"
        elif isinstance(message, str):
            body = message.encode("utf-8")
            content_type = "text/plain"
        else:
            body = message
            content_type = "application/octet-stream"

        outgoing = Message(
            body=body,
            content_type=content_type,
            delivery_mode=DeliveryMode.PERSISTENT,
            message_id=message_id or uuid4().hex,
            correlation_id=correlation_id,
            headers=headers,
        )
        exchange = (
            self.publish_channel.default_exchange
            if not exchange_name
            else await self.declare_exchange(exchange_name)
        )
        await exchange.publish(
            outgoing,
            routing_key=routing_key,
            mandatory=True,
            timeout=self.timeout,
        )
        logger.debug("Published RabbitMQ message to %s:%s", exchange_name, routing_key)

    async def consume_messages(
        self,
        queue_name: str,
        on_message_callback: MessageHandler,
        *,
        auto_ack: bool = False,
        requeue_on_error: bool = False,
    ) -> str:
        """Register an async consumer; return a local registration ID.

        The callback receives an aio-pika IncomingMessage rather than Pika's
        (channel, method, properties, body) callback arguments.
        """
        if auto_ack:
            raise ValueError("auto_ack=True is disabled for reliable CAOS consumers")
        queue = await self.declare_queue(queue_name)

        async def _on_message(message: AbstractIncomingMessage) -> None:
            try:
                async with message.process(requeue=requeue_on_error):
                    await on_message_callback(message)
            except Exception:
                logger.exception("RabbitMQ handler failed for queue=%s", queue_name)
                # The processing context negatively acknowledges on errors.
                # For requeue_on_error=False, configure a DLX if failures
                # should be retained; otherwise RabbitMQ discards them.

        consumer_tag = await queue.consume(_on_message, no_ack=False)
        registration_id = uuid4().hex
        self._consumers[registration_id] = (queue, consumer_tag)
        logger.info("RabbitMQ consumer registered queue=%s", queue_name)
        return registration_id

    async def stop_consuming(self, registration_id: str | None = None) -> None:
        """Cancel one or all managed consumers."""
        ids = [registration_id] if registration_id is not None else list(self._consumers)
        errors: list[BaseException] = []
        for key in ids:
            entry = self._consumers.get(key)
            if entry is None:
                continue
            queue, consumer_tag = entry
            try:
                await queue.cancel(consumer_tag)
            except BaseException as error:
                errors.append(error)
                logger.exception("Failed to cancel RabbitMQ consumer %s", key)
            else:
                self._consumers.pop(key, None)
        if errors:
            raise errors[0]

    async def purge_queue(self, queue_name: str) -> None:
        queue = await self.declare_queue(queue_name)
        await queue.purge()

    async def delete_queue(self, queue_name: str) -> None:
        queue = await self.declare_queue(queue_name)
        await queue.delete()

    async def close_connection(self) -> None:
        await self.stop()

    async def stop(self) -> None:
        self._closing = True
        # Serialize with connect(), but allow retries of unfinished shutdown.
        async with self._lock:
            errors: list[BaseException] = []
            try:
                await self.stop_consuming()
            except BaseException as error:
                errors.append(error)
                logger.exception("Failed stopping RabbitMQ consumers")
            errors.extend(await self._close_resources())
            if errors:
                raise errors[0]
        logger.info("RabbitMQ client stopped")

    async def _close_resources(self) -> list[BaseException]:
        """Attempt every close; retain failed handles unless their connection closes."""
        errors: list[BaseException] = []
        for name in ("consumer_channel", "publish_channel", "connection"):
            resource = getattr(self, name)
            if resource is None:
                continue
            try:
                if not resource.is_closed:
                    await resource.close()
            except BaseException as error:
                errors.append(error)
                logger.exception("Failed closing RabbitMQ %s", name)
            else:
                setattr(self, name, None)
                if name == "connection":
                    # Closing the connection also disposes its channels/consumers.
                    self.consumer_channel = None
                    self.publish_channel = None
                    self._consumers.clear()
        return errors
