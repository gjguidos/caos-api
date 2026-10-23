import asyncio
from unittest.mock import AsyncMock

import pytest

from src.base.infrastructure.messaging.rabbitmq import rabbitmq_client as client_module
from src.base.infrastructure.messaging.rabbitmq.rabbitmq_client import RabbitMQClient


def resource() -> AsyncMock:
    mock = AsyncMock()
    mock.is_closed = False
    return mock


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["connection", "publish", "consumer", "qos"])
@pytest.mark.parametrize("cancelled", [False, True])
async def test_partial_connect_rolls_back(
    monkeypatch: pytest.MonkeyPatch, stage: str, cancelled: bool
) -> None:
    client = RabbitMQClient("amqp://unused")
    connection, publish, consumer = resource(), resource(), resource()
    failure = asyncio.CancelledError("cancelled") if cancelled else RuntimeError("connect failed")
    connect = AsyncMock(return_value=connection)
    if stage == "connection":
        connect.side_effect = failure
    connection.channel.side_effect = [
        failure if stage == "publish" else publish,
        failure if stage == "consumer" else consumer,
    ]
    if stage == "qos":
        consumer.set_qos.side_effect = failure
    monkeypatch.setattr(client_module.aio_pika, "connect_robust", connect)
    with pytest.raises(type(failure)) as caught:
        await client.connect()
    assert caught.value is failure
    assert connection.close.await_count == (0 if stage == "connection" else 1)
    assert publish.close.await_count == (1 if stage in {"consumer", "qos"} else 0)
    assert consumer.close.await_count == (1 if stage == "qos" else 0)
    assert client.connection is client.publish_channel is client.consumer_channel is None
    assert not client._closing
    # A failed connect, unlike explicit shutdown, does not prohibit reconnecting.
    connect.side_effect = None
    connection.channel.side_effect = [publish, consumer]
    consumer.set_qos.side_effect = None
    await client.connect()
    assert client.connection is connection
    await client.stop()


@pytest.mark.asyncio
async def test_connect_failure_preserved_when_rollback_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = RabbitMQClient("amqp://unused")
    connection, publish = resource(), resource()
    original = RuntimeError("consumer channel failed")
    connection.channel.side_effect = [publish, original]
    publish.close.side_effect = RuntimeError("publish close failed")
    connection.close.side_effect = RuntimeError("connection close failed")
    monkeypatch.setattr(
        client_module.aio_pika, "connect_robust", AsyncMock(return_value=connection)
    )
    with pytest.raises(RuntimeError) as caught:
        await client.connect()
    assert caught.value is original
    assert client.connection is connection
    assert client.publish_channel is publish
    publish.close.side_effect = connection.close.side_effect = None
    await client.stop()
    assert client.connection is client.publish_channel is None


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_resource", ["consumer_channel", "publish_channel", "connection"])
@pytest.mark.parametrize("cancelled", [False, True])
async def test_stop_attempts_all_closes_and_is_repeatable(
    failed_resource: str, cancelled: bool
) -> None:
    client = RabbitMQClient("amqp://unused")
    resources = {name: resource() for name in ("consumer_channel", "publish_channel", "connection")}
    for name, mock in resources.items():
        setattr(client, name, mock)
    failure = asyncio.CancelledError("cancelled") if cancelled else RuntimeError("close failed")
    resources[failed_resource].close.side_effect = failure
    with pytest.raises(type(failure)) as caught:
        await client.stop()
    assert caught.value is failure
    for mock in resources.values():
        mock.close.assert_awaited_once()
    assert client._closing
    if failed_resource == "connection":
        assert client.connection is resources["connection"]
    else:
        # A successful connection close also disposes channels whose close raised.
        assert client.consumer_channel is client.publish_channel is None
    resources[failed_resource].close.side_effect = None
    await client.stop()
    assert client.connection is client.consumer_channel is client.publish_channel is None
    await client.stop()
    with pytest.raises(RuntimeError, match="shutting down"):
        await client.connect()


@pytest.mark.asyncio
async def test_stop_retries_only_unfinished_handles() -> None:
    client = RabbitMQClient("amqp://unused")
    consumer, publish, connection = resource(), resource(), resource()
    client.consumer_channel, client.publish_channel, client.connection = (
        consumer,
        publish,
        connection,
    )
    consumer.close.side_effect = RuntimeError("consumer close failed")
    connection.close.side_effect = RuntimeError("connection close failed")
    with pytest.raises(RuntimeError, match="consumer close failed"):
        await client.stop()
    assert client.consumer_channel is consumer
    assert client.publish_channel is None
    assert client.connection is connection
    consumer.close.side_effect = connection.close.side_effect = None
    await client.stop()
    assert consumer.close.await_count == connection.close.await_count == 2
    publish.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [False, True])
async def test_failed_cancellation_retains_registration_and_attempts_others(
    cancelled: bool,
) -> None:
    client = RabbitMQClient("amqp://unused")
    first, other = AsyncMock(), AsyncMock()
    failure = asyncio.CancelledError("cancelled") if cancelled else RuntimeError("cancel failed")
    first.cancel.side_effect = failure
    client._consumers.update({"first": (first, "tag-first"), "other": (other, "tag-other")})
    with pytest.raises(type(failure)) as caught:
        await client.stop_consuming()
    assert caught.value is failure
    other.cancel.assert_awaited_once_with("tag-other")
    assert client._consumers == {"first": (first, "tag-first")}
    first.cancel.side_effect = None
    await client.stop_consuming("first")
    assert client._consumers == {}


@pytest.mark.asyncio
async def test_failed_cancellation_does_not_skip_connection_close() -> None:
    client = RabbitMQClient("amqp://unused")
    queue, connection = AsyncMock(), resource()
    client.connection = connection
    queue.cancel.side_effect = RuntimeError("cancel failed")
    client._consumers["registration"] = (queue, "tag")
    with pytest.raises(RuntimeError, match="cancel failed"):
        await client.stop()
    connection.close.assert_awaited_once()
    assert client._consumers == {}
    await client.stop()
