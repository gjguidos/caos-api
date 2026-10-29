"""Typed ``store.create`` contracts. Callers name a logical resource, never storage."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from src.base.operations.v2.admission import AuthorizationProvenance, Digest

StoreResourceType = Literal["agent"]


class StoreError(Exception):
    """User-safe store failure; messages never contain payloads or storage details."""

    code = "STORE_ERROR"
    retryable = False


class UnsupportedResource(StoreError):
    code = "STORE_UNSUPPORTED_RESOURCE"


class InvalidStorePayload(StoreError):
    code = "STORE_INVALID_PAYLOAD"


class StoreOwnershipViolation(StoreError):
    """Payload ownership disagrees with the trusted write context (spoofing/escalation)."""

    code = "STORE_OWNERSHIP_VIOLATION"


class StoreIdempotencyConflict(StoreError):
    code = "STORE_IDEMPOTENCY_CONFLICT"


class StoreConflict(StoreError):
    """The resource identity exists outside this idempotency scope."""

    code = "STORE_CONFLICT"


class AmbiguousStoreWrite(StoreError):
    """The write may or may not have committed: repeat with the same idempotency key."""

    code = "STORE_AMBIGUOUS_WRITE"
    retryable = True


class StoreUnavailable(StoreError):
    """Required storage guarantees (e.g. the unique index) could not be established."""

    code = "STORE_UNAVAILABLE"
    retryable = True


class StoreCreateRequest(BaseModel):
    """Caller envelope. Tenant, owner, creator and authority are never read from it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    resource_type: StoreResourceType
    payload: dict[str, Any]
    idempotency_key: Annotated[str, Field(min_length=1, max_length=200, pattern=r"^\S(.*\S)?$")]


class StoreCreateResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    resource_type: StoreResourceType
    resource_id: str
    created: bool  # False: an identical earlier request already persisted it.
    request_digest: Digest


@dataclass(frozen=True)
class StoreWriteContext:
    """Trusted runtime authority for one write; constructed only by trusted code.

    ``ownership`` is decided by the authority, never the payload. Capability calls
    always write tenant resources; ``system`` is reserved for a future, explicitly
    reviewed bootstrap authority. ``verify`` rechecks live authority immediately
    before the write and must raise when it no longer holds.
    """

    provenance: AuthorizationProvenance
    verify: Callable[[], Awaitable[None]]
    ownership: Literal["tenant", "system"] = "tenant"


class ResourceStore(Protocol):
    """Approved typed store for one logical resource type; no generic database access."""

    resource_type: StoreResourceType

    async def create(
        self, payload: dict[str, Any], *, idempotency_key: str, context: StoreWriteContext
    ) -> StoreCreateResult: ...
