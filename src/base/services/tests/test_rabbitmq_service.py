import asyncio
import hashlib
import hmac
import json
from unittest.mock import AsyncMock, create_autospec

import pytest
from pydantic import BaseModel

from src.base.events.v2.contracts import EventPermissionError
from src.base.events.v2.policy import CognitiveEventPolicy, EventGrants, EventPrincipal
from src.base.events.v2.registry import CognitiveEventRegistry, EventContract, Subscription
from src.base.events.v2.runtime import CognitiveEventRuntime
from src.base.infrastructure.messaging.rabbitmq.rabbitmq_client import RabbitMQClient
from src.base.repositories.rabbitmq_repository import RabbitMQRepository
from src.base.services.rabbitmq_service import RabbitMQService

EVENT_TYPE = "test.messaging"
SIGNING_KEY = b"test-signing-key-not-a-secret-1234"


class Payload(BaseModel):
    text: str


@pytest.fixture
def service_setup() -> tuple[RabbitMQService, AsyncMock]:
    client = create_autospec(RabbitMQClient, instance=True)
    return RabbitMQService(RabbitMQRepository(client)), client


@pytest.mark.asyncio
async def test_publish_forwards_metadata(service_setup: tuple[RabbitMQService, AsyncMock]) -> None:
    service, client = service_setup
    body = {"text": "original"}
    headers = {"caos-signature": "signature", "trace": "trace-id"}
    await service.publish_event(
        "events", "event", body, "correlation", message_id="identity", headers=headers
    )
    client.publish_event.assert_awaited_once_with(
        exchange_name="events",
        routing_key="event",
        message=body,
        correlation_id="correlation",
        message_id="identity",
        headers=headers,
    )
    assert client.publish_event.await_args.kwargs["headers"] is headers
    assert client.publish_event.await_args.kwargs["message"] is body


@pytest.mark.asyncio
@pytest.mark.parametrize("style", ["omitted", "positional", "keyword"])
async def test_legacy_publish_calls(
    service_setup: tuple[RabbitMQService, AsyncMock], style: str
) -> None:
    service, client = service_setup
    body = {"text": "legacy"}
    if style == "omitted":
        await service.publish_event("events", "event", body)
    elif style == "positional":
        await service.publish_event("events", "event", body, "correlation")
    else:
        await service.publish_event("events", "event", body, correlation_id="correlation")
    client.publish_event.assert_awaited_once_with(
        exchange_name="events",
        routing_key="event",
        message=body,
        correlation_id=None if style == "omitted" else "correlation",
        message_id=None,
        headers=None,
    )


@pytest.mark.asyncio
async def test_targeted_cancellation_preserves_other_consumers() -> None:
    client = RabbitMQClient("amqp://unused")
    first, other = AsyncMock(), AsyncMock()
    client._consumers.update({"first": (first, "tag-first"), "other": (other, "tag-other")})
    service = RabbitMQService(RabbitMQRepository(client))
    await service.stop_consuming("first")
    first.cancel.assert_awaited_once_with("tag-first")
    other.cancel.assert_not_awaited()
    assert client._consumers == {"other": (other, "tag-other")}
    assert not client._closing
    await service.stop_consuming("first")
    first.cancel.assert_awaited_once()
    await service.stop_consuming()
    other.cancel.assert_awaited_once_with("tag-other")
    assert client._consumers == {}


@pytest.mark.asyncio
async def test_runtime_metadata_reaches_amqp_message(monkeypatch: pytest.MonkeyPatch) -> None:
    client = RabbitMQClient("amqp://unused")
    channel = AsyncMock()
    client.publish_channel = channel
    monkeypatch.setattr(client, "_ensure_connected", AsyncMock())
    service = RabbitMQService(RabbitMQRepository(client))
    registry = CognitiveEventRegistry()
    registry.register_contract(EventContract(EVENT_TYPE, Payload))
    runtime = CognitiveEventRuntime(
        service,
        registry,
        CognitiveEventPolicy({"publisher": EventGrants(publish=frozenset({EVENT_TYPE}))}),
        signing_key=SIGNING_KEY,
    )
    event = await runtime.bind(EventPrincipal("publisher", "tenant")).emit(
        EVENT_TYPE, {"text": "café"}, correlation_id="correlation", causation_id="cause"
    )
    exchange = channel.declare_exchange.return_value
    outgoing = exchange.publish.await_args.args[0]
    assert json.loads(outgoing.body) == event.model_dump(mode="json")
    assert outgoing.message_id == event.event_id
    assert outgoing.correlation_id == "correlation"
    canonical = json.dumps(event.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    assert (
        outgoing.headers["caos-signature"]
        == hmac.new(SIGNING_KEY, canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    )
    assert exchange.publish.await_args.kwargs["routing_key"] == EVENT_TYPE
    with pytest.raises(EventPermissionError, match="Publish denied"):
        await runtime.bind(EventPrincipal("unauthorized", "tenant")).emit(
            EVENT_TYPE, {"text": "denied"}
        )
    exchange.publish.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["subscribe", "setup", "authorization", "cancelled"])
async def test_partial_subscription_rollback(
    service_setup: tuple[RabbitMQService, AsyncMock], failure_stage: str
) -> None:
    service, client = service_setup
    registry = CognitiveEventRegistry()
    registry.register_contract(EventContract(EVENT_TYPE, Payload))
    handler = AsyncMock()
    for index in range(3):
        registry.register_subscription(
            Subscription(f"consumer-{index}", EVENT_TYPE, handler, f"queue-{index}")
        )
    grants = {
        f"consumer-{index}": EventGrants(subscribe=frozenset({EVENT_TYPE}))
        for index in range(3)
        if failure_stage != "authorization" or index != 2
    }
    policy = CognitiveEventPolicy(grants)
    runtime = CognitiveEventRuntime(service, registry, policy, signing_key=SIGNING_KEY)
    failure: BaseException = RuntimeError("subscription failed")
    if failure_stage in {"subscribe", "cancelled"}:
        if failure_stage == "cancelled":
            failure = asyncio.CancelledError("startup cancelled")
        client.consume_messages.side_effect = ["first", "second", failure]
    else:
        client.consume_messages.side_effect = ["first", "second"]
        if failure_stage == "setup":
            client.declare_exchange.side_effect = [None, None, failure]
    expected_error = EventPermissionError if failure_stage == "authorization" else type(failure)
    with pytest.raises(expected_error) as caught:
        await runtime.start()
    if failure_stage != "authorization":
        assert caught.value is failure
    assert [call.args for call in client.stop_consuming.await_args_list] == [
        ("second",),
        ("first",),
    ]
    client.stop.assert_not_awaited()
    # Rollback clears runtime registrations; stopping again must not cancel twice.
    await runtime.stop()
    assert client.stop_consuming.await_count == 2
    # Transport failures can be retried without changing the policy or runtime.
    if failure_stage == "authorization":
        return
    client.declare_exchange.side_effect = None
    client.consume_messages.side_effect = ["new-first", "new-second", "new-third"]
    await runtime.start()
    await runtime.start()
    expected_attempts = 6 if failure_stage in {"subscribe", "cancelled"} else 5
    assert client.consume_messages.await_count == expected_attempts
    await runtime.stop()
    assert [call.args[0] for call in client.stop_consuming.await_args_list] == [
        "second",
        "first",
        "new-third",
        "new-second",
        "new-first",
    ]
