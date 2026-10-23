import asyncio
from unittest.mock import AsyncMock, call

import pytest
from pydantic import BaseModel

from src.base.events.v2.contracts import EventPermissionError
from src.base.events.v2.policy import CognitiveEventPolicy, EventGrants
from src.base.events.v2.registry import CognitiveEventRegistry, EventContract, Subscription
from src.base.events.v2.runtime import CognitiveEventRuntime, EventMessaging
from src.base.infrastructure.messaging.rabbitmq.rabbitmq_client import RabbitMQClient
from src.base.repositories.rabbitmq_repository import RabbitMQRepository
from src.base.services.rabbitmq_service import RabbitMQService


@pytest.fixture
def lifecycle_setup() -> tuple[CognitiveEventRuntime, AsyncMock]:
    messaging = AsyncMock(spec=EventMessaging)
    messaging.subscribe.side_effect = ["first-id", "second-id", "third-id"]
    registry = CognitiveEventRegistry()
    registry.register_contract(EventContract("test.lifecycle", BaseModel))
    grants = {}
    for name in ("first", "second", "third"):
        registry.register_subscription(Subscription(name, "test.lifecycle", AsyncMock(), name))
        grants[name] = EventGrants(subscribe=frozenset({"test.lifecycle"}))
    runtime = CognitiveEventRuntime(
        messaging, registry, CognitiveEventPolicy(grants), signing_key=b"x" * 32
    )
    return runtime, messaging


@pytest.mark.asyncio
@pytest.mark.parametrize("cancelled", [False, True])
@pytest.mark.parametrize("failed_ids", [("second-id",), ("first-id", "third-id")])
async def test_failed_stop_retains_ids_retries_and_blocks_duplicate_start(
    lifecycle_setup: tuple[CognitiveEventRuntime, AsyncMock],
    cancelled: bool,
    failed_ids: tuple[str, ...],
    caplog: pytest.LogCaptureFixture,
) -> None:
    runtime, messaging = lifecycle_setup
    await runtime.start()
    failure = (
        asyncio.CancelledError("cancel failed") if cancelled else RuntimeError("cancel failed")
    )

    async def cancel(registration_id: str) -> None:
        if registration_id in failed_ids:
            raise failure

    messaging.stop_consuming.side_effect = cancel
    with pytest.raises(type(failure)) as caught:
        await runtime.stop()
    assert caught.value is failure
    assert messaging.stop_consuming.await_args_list == [
        call("third-id"),
        call("second-id"),
        call("first-id"),
    ]
    assert runtime._registrations == list(failed_ids)
    for registration_id in failed_ids:
        assert f"Failed stopping consumer {registration_id}" in caplog.text

    # An incomplete shutdown is not a new startup: no extra consumers may be registered.
    messaging.setup_event_queue.reset_mock()
    messaging.subscribe.reset_mock()
    await runtime.start()
    messaging.setup_event_queue.assert_not_awaited()
    messaging.subscribe.assert_not_awaited()

    # Another failure retries only the outstanding IDs, preserving their original order.
    messaging.stop_consuming.reset_mock()
    with pytest.raises(type(failure)):
        await runtime.stop()
    expected_retry = [call(registration_id) for registration_id in reversed(failed_ids)]
    assert messaging.stop_consuming.await_args_list == expected_retry
    assert runtime._registrations == list(failed_ids)

    messaging.stop_consuming.reset_mock()
    messaging.stop_consuming.side_effect = None
    await runtime.stop()
    assert messaging.stop_consuming.await_args_list == expected_retry
    assert runtime._registrations == []
    await runtime.stop()
    assert messaging.stop_consuming.await_args_list == expected_retry

    # Once cancellation succeeds, ordinary startup and idempotent start work again.
    messaging.subscribe.side_effect = ["new-first", "new-second", "new-third"]
    await runtime.start()
    await runtime.start()
    assert messaging.subscribe.await_count == 3
    await runtime.stop()
    assert runtime._registrations == []
    assert messaging.stop_consuming.await_args_list[-3:] == [
        call("new-third"),
        call("new-second"),
        call("new-first"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("startup_cancelled", [False, True])
@pytest.mark.parametrize("rollback_cancelled", [False, True])
async def test_startup_failure_survives_failed_rollback(
    lifecycle_setup: tuple[CognitiveEventRuntime, AsyncMock],
    startup_cancelled: bool,
    rollback_cancelled: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    runtime, messaging = lifecycle_setup
    original = (
        asyncio.CancelledError("startup cancelled")
        if startup_cancelled
        else RuntimeError("startup failed")
    )
    rollback_error = (
        asyncio.CancelledError("rollback cancelled")
        if rollback_cancelled
        else RuntimeError("rollback failed")
    )
    messaging.subscribe.side_effect = ["first-id", "second-id", original]

    async def cancel(registration_id: str) -> None:
        if registration_id == "second-id":
            raise rollback_error

    messaging.stop_consuming.side_effect = cancel
    with pytest.raises(type(original)) as caught:
        await runtime.start()
    assert caught.value is original
    assert messaging.stop_consuming.await_args_list == [call("second-id"), call("first-id")]
    assert runtime._registrations == ["second-id"]
    assert "Failed stopping consumer second-id" in caplog.text
    assert "Failed rolling back cognitive event runtime startup" in caplog.text
    messaging.subscribe.reset_mock()
    await runtime.start()
    messaging.subscribe.assert_not_awaited()
    messaging.stop_consuming.reset_mock()
    messaging.stop_consuming.side_effect = None
    await runtime.stop()
    messaging.stop_consuming.assert_awaited_once_with("second-id")
    assert runtime._registrations == []
    messaging.subscribe.side_effect = ["new-first", "new-second", "new-third"]
    await runtime.start()
    assert messaging.subscribe.await_count == 3
    await runtime.stop()


@pytest.mark.asyncio
async def test_successful_lifecycle_is_idempotent(
    lifecycle_setup: tuple[CognitiveEventRuntime, AsyncMock],
) -> None:
    runtime, messaging = lifecycle_setup
    await runtime.start()
    await runtime.start()
    assert messaging.subscribe.await_count == 3
    await runtime.stop()
    await runtime.stop()
    assert messaging.stop_consuming.await_args_list == [
        call("third-id"),
        call("second-id"),
        call("first-id"),
    ]
    messaging.subscribe.side_effect = ["new-first", "new-second", "new-third"]
    await runtime.start()
    assert messaging.subscribe.await_count == 6
    await runtime.stop()


@pytest.mark.asyncio
async def test_start_still_requires_subscribe_authorization() -> None:
    messaging = AsyncMock(spec=EventMessaging)
    registry = CognitiveEventRegistry()
    registry.register_contract(EventContract("test.lifecycle", BaseModel))
    registry.register_subscription(Subscription("denied", "test.lifecycle", AsyncMock(), "queue"))
    runtime = CognitiveEventRuntime(
        messaging, registry, CognitiveEventPolicy({}), signing_key=b"x" * 32
    )
    with pytest.raises(EventPermissionError, match="Subscribe denied"):
        await runtime.start()
    messaging.setup_event_queue.assert_not_awaited()
    messaging.subscribe.assert_not_awaited()
    messaging.stop_consuming.assert_not_awaited()


@pytest.mark.asyncio
async def test_runtime_and_client_retain_same_failed_registration(
    lifecycle_setup: tuple[CognitiveEventRuntime, AsyncMock],
) -> None:
    runtime, messaging = lifecycle_setup
    await runtime.start()
    client = RabbitMQClient("amqp://unused")
    queues = {registration_id: AsyncMock() for registration_id in runtime._registrations}
    client._consumers.update(
        {registration_id: (queue, registration_id) for registration_id, queue in queues.items()}
    )
    service = RabbitMQService(RabbitMQRepository(client))
    messaging.stop_consuming.side_effect = service.stop_consuming
    queues["second-id"].cancel.side_effect = RuntimeError("broker cancellation failed")
    with pytest.raises(RuntimeError, match="broker cancellation failed"):
        await runtime.stop()
    assert runtime._registrations == list(client._consumers) == ["second-id"]
    await runtime.start()
    assert messaging.subscribe.await_count == 3
    queues["second-id"].cancel.side_effect = None
    await runtime.stop()
    assert runtime._registrations == []
    assert client._consumers == {}
    assert queues["second-id"].cancel.await_count == 2
    queues["first-id"].cancel.assert_awaited_once()
    queues["third-id"].cancel.assert_awaited_once()
    assert not client._closing
