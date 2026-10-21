from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pydantic import BaseModel
from .contracts import EventEnvelope, EventContractError

EventHandler = Callable[[EventEnvelope], Awaitable[None]]


@dataclass(frozen=True)
class EventContract:
    event_type: str
    payload_model: type[BaseModel]
    version: int = 1


@dataclass(frozen=True)
class Subscription:
    subscriber_id: str
    event_type: str
    handler: EventHandler
    queue_name: str


class CognitiveEventRegistry:
    def __init__(self) -> None:
        self._contracts: dict[str, EventContract] = {}
        self._subscriptions: dict[tuple[str, str], Subscription] = {}

    def register_contract(self, contract: EventContract) -> None:
        if contract.event_type in self._contracts:
            raise ValueError(f"Event contract already registered: {contract.event_type}")
        self._contracts[contract.event_type] = contract

    def validate(self, event_type: str, version: int, payload: dict) -> dict:
        contract = self._contracts.get(event_type)
        if contract is None or contract.version != version:
            raise EventContractError(f"Unknown event/version: {event_type}@{version}")
        return contract.payload_model.model_validate(payload).model_dump(mode="json")

    def register_subscription(self, subscription: Subscription) -> None:
        if subscription.event_type not in self._contracts:
            raise EventContractError(f"No contract: {subscription.event_type}")
        key = (subscription.subscriber_id, subscription.event_type)
        if key in self._subscriptions:
            raise ValueError(f"Duplicate subscription: {key}")
        self._subscriptions[key] = subscription

    def subscriptions(self) -> tuple[Subscription, ...]:
        return tuple(self._subscriptions.values())
