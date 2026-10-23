import hashlib
import hmac
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel, SecretStr

from src.base.events.v2.contracts import EventPermissionError
from src.base.events.v2.policy import CognitiveEventPolicy, EventGrants, EventPrincipal
from src.base.events.v2.registry import CognitiveEventRegistry, EventContract, Subscription
from src.base.events.v2.runtime import CognitiveEventRuntime, EventMessaging

EVENT_TYPE = "test.signature"
SIGNING_KEY = b"test-signing-key-not-a-secret-1234"


class Payload(BaseModel):
    text: str


@pytest.fixture
def event_setup() -> tuple[CognitiveEventRuntime, AsyncMock, AsyncMock]:
    messaging = AsyncMock(spec=EventMessaging)
    messaging.subscribe.return_value = "registration-1"
    handler = AsyncMock()
    registry = CognitiveEventRegistry()
    registry.register_contract(EventContract(EVENT_TYPE, Payload))
    registry.register_subscription(Subscription("consumer", EVENT_TYPE, handler, "test.queue"))
    policy = CognitiveEventPolicy(
        {
            "publisher": EventGrants(publish=frozenset({EVENT_TYPE})),
            "consumer": EventGrants(subscribe=frozenset({EVENT_TYPE})),
        }
    )
    runtime = CognitiveEventRuntime(messaging, registry, policy, signing_key=SIGNING_KEY)
    return runtime, messaging, handler


@pytest.mark.parametrize(
    "key",
    ["x" * 32, SecretStr("x" * 32), bytearray(b"x" * 32), memoryview(b"x" * 32), None, 32],
    ids=["str", "secret-str", "bytearray", "memoryview", "none", "int"],
)
def test_rejects_non_bytes_keys(key: Any) -> None:
    with pytest.raises(TypeError, match="signing_key must be bytes"):
        CognitiveEventRuntime(
            AsyncMock(spec=EventMessaging),
            CognitiveEventRegistry(),
            CognitiveEventPolicy({}),
            signing_key=key,
        )


@pytest.mark.parametrize("length", [0, 1, 31])
def test_rejects_short_byte_keys(length: int) -> None:
    with pytest.raises(ValueError, match="signing_key must be >= 32 bytes"):
        CognitiveEventRuntime(
            AsyncMock(spec=EventMessaging),
            CognitiveEventRegistry(),
            CognitiveEventPolicy({}),
            signing_key=b"x" * length,
        )


@pytest.mark.parametrize("key", [b"x" * 32, b"x" * 64, ("é" * 16).encode("utf-8")])
def test_accepts_keys_by_byte_length(key: bytes) -> None:
    CognitiveEventRuntime(
        AsyncMock(spec=EventMessaging),
        CognitiveEventRegistry(),
        CognitiveEventPolicy({}),
        signing_key=key,
    )


@pytest.mark.asyncio
async def test_published_signature_verifies_and_preserves_identity(
    event_setup: tuple[CognitiveEventRuntime, AsyncMock, AsyncMock],
) -> None:
    runtime, messaging, handler = event_setup
    await runtime.start()
    event = await runtime.bind(EventPrincipal("publisher", "tenant")).emit(
        EVENT_TYPE, {"text": "café"}, correlation_id="correlation", causation_id="cause"
    )
    published = messaging.publish_event.await_args.kwargs
    encoded = json.dumps(published["message"], sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    assert published["headers"] == {
        "caos-signature": hmac.new(SIGNING_KEY, encoded, hashlib.sha256).hexdigest()
    }
    assert published["message"] == event.model_dump(mode="json")
    assert published["message_id"] == event.event_id
    assert published["correlation_id"] == "correlation"
    assert published["message"]["causation_id"] == "cause"
    callback = messaging.subscribe.await_args.args[1]
    await callback(
        SimpleNamespace(
            body=json.dumps(published["message"]).encode(), headers=published["headers"]
        )
    )
    handler.assert_awaited_once_with(event)
    await runtime.stop()
    messaging.stop_consuming.assert_awaited_once_with("registration-1")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tampering", ["payload", "actor", "correlation", "signature", "missing", "non-string"]
)
async def test_rejects_tampering_before_dispatch(
    event_setup: tuple[CognitiveEventRuntime, AsyncMock, AsyncMock], tampering: str
) -> None:
    runtime, messaging, handler = event_setup
    await runtime.start()
    await runtime.bind(EventPrincipal("publisher", "tenant")).emit(EVENT_TYPE, {"text": "original"})
    published = messaging.publish_event.await_args.kwargs
    body = published["message"]
    headers = dict(published["headers"])
    if tampering == "payload":
        body["payload"]["text"] = "tampered"
    elif tampering == "actor":
        body["actor_id"] = "other"
    elif tampering == "correlation":
        body["correlation_id"] = "other"
    elif tampering == "signature":
        headers["caos-signature"] = "0" * 64
    elif tampering == "missing":
        headers = {}
    else:
        headers["caos-signature"] = b"not-a-string"
    callback = messaging.subscribe.await_args.args[1]
    with pytest.raises(EventPermissionError, match="Invalid or missing event signature"):
        await callback(SimpleNamespace(body=json.dumps(body).encode(), headers=headers))
    handler.assert_not_awaited()
    await runtime.stop()


@pytest.mark.asyncio
async def test_unauthorized_publish_never_reaches_transport(
    event_setup: tuple[CognitiveEventRuntime, AsyncMock, AsyncMock],
) -> None:
    runtime, messaging, _ = event_setup
    with pytest.raises(EventPermissionError, match="Publish denied"):
        await runtime.bind(EventPrincipal("unauthorized", "tenant")).emit(
            EVENT_TYPE, {"text": "denied"}
        )
    messaging.publish_event.assert_not_awaited()
