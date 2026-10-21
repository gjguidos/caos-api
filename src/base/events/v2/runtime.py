from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any, Protocol

from .contracts import EventEnvelope, EventContractError, EventPermissionError
from .policy import CognitiveEventPolicy, EventPrincipal
from .registry import CognitiveEventRegistry, Subscription

logger = logging.getLogger(__name__)


class EventMessaging(Protocol):
    async def setup_event_queue(self, exchange_name: str, queue_name: str, routing_key: str) -> None: ...
    async def publish_event(self, exchange_name: str, routing_key: str, message: dict[str, Any], *, correlation_id: str | None = None, message_id: str | None = None, headers: dict[str, Any] | None = None) -> None: ...
    async def subscribe(self, queue_name: str, handler: Any) -> str: ...
    async def stop_consuming(self, registration_id: str | None = None) -> None: ...


class AuthorizedEventClient:
    """Issued by trusted factory/bootstrap; no broker credentials or mutable grants."""

    def __init__(self, runtime: CognitiveEventRuntime, principal: EventPrincipal) -> None:
        self._runtime = runtime
        self._principal = principal

    async def emit(self, event_type: str, payload: dict[str, Any], *, correlation_id: str | None = None, causation_id: str | None = None) -> EventEnvelope:
        return await self._runtime.publish(
            principal=self._principal,
            event_type=event_type,
            payload=payload,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )


class CognitiveEventRuntime:
    """V2 broker-backed, policy-enforced event dispatch, independently of AOSA V1.

    Consumer failures propagate to RabbitMQ callback so it can nack/DLQ.
    Message authenticity is enforced with HMAC over canonical event JSON.
    """

    def __init__(self, messaging: EventMessaging, registry: CognitiveEventRegistry, policy: CognitiveEventPolicy, *, signing_key: bytes, exchange: str = "caos.events.v2") -> None:
        if len(signing_key) < 32:
            raise ValueError("signing_key must be >= 32 bytes and sourced from secret configuration")
        self._messaging = messaging
        self._registry = registry
        self._policy = policy
        self._key = signing_key
        self._exchange = exchange
        self._registrations: list[str] = []

    def bind(self, principal: EventPrincipal) -> AuthorizedEventClient:
        # The caller of bind MUST be trusted bootstrap/factory code, never the LLM or API request body.
        return AuthorizedEventClient(self, principal)

    def _sign(self, event: EventEnvelope) -> str:
        encoded = json.dumps(event.model_dump(mode="json"), sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hmac.new(self._key, encoded, hashlib.sha256).hexdigest()

    async def publish(self, *, principal: EventPrincipal, event_type: str, payload: dict[str, Any], correlation_id: str | None = None, causation_id: str | None = None) -> EventEnvelope:
        self._policy.require_publish(principal, event_type)
        normalized = self._registry.validate(event_type, 1, payload)
        args = dict(event_type=event_type, actor_id=principal.agent_id, tenant_id=principal.tenant_id, payload=normalized, causation_id=causation_id)
        if correlation_id is not None:
            args["correlation_id"] = correlation_id
        event = EventEnvelope(**args)
        await self._messaging.publish_event(
            exchange_name=self._exchange,
            routing_key=event_type,
            message=event.model_dump(mode="json"),
            correlation_id=event.correlation_id,
            message_id=event.event_id,
            headers={"caos-signature": self._sign(event)},
        )
        return event

    async def start(self) -> None:
        if self._registrations:
            return
        try:
            for sub in self._registry.subscriptions():
                self._policy.require_subscribe(sub.subscriber_id, sub.event_type)
                await self._messaging.setup_event_queue(self._exchange, sub.queue_name, sub.event_type)
                async def _handle(message: Any, subscription: Subscription = sub) -> None:
                    data = json.loads(message.body)
                    event = EventEnvelope.model_validate(data)
                    signature = (message.headers or {}).get("caos-signature", "")
                    if not isinstance(signature, str) or not hmac.compare_digest(self._sign(event), signature):
                        raise EventPermissionError("Invalid or missing event signature")
                    if event.event_type != subscription.event_type:
                        raise EventContractError("Event type does not match consumer binding")
                    self._policy.require_publish(EventPrincipal(event.actor_id, event.tenant_id), event.event_type)
                    self._policy.require_subscribe(subscription.subscriber_id, event.event_type)
                    self._registry.validate(event.event_type, event.schema_version, event.payload)
                    # Handler must be idempotent: duplicate delivery is possible.
                    await subscription.handler(event)
                registration_id = await self._messaging.subscribe(sub.queue_name, _handle)
                self._registrations.append(registration_id)
        except BaseException:
            await self.stop()
            raise

    async def stop(self) -> None:
        for registration_id in reversed(self._registrations):
            try:
                await self._messaging.stop_consuming(registration_id)
            except Exception:
                logger.exception("Failed stopping consumer %s", registration_id)
        self._registrations.clear()
