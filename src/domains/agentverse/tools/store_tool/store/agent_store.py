"""Approved store for persisted ``Agent`` definitions (the authoritative existing model).

Server-controlled fields (identity, ownership, tenant, creator, exposure) are derived
from the trusted write context; a payload may omit them or repeat the exact derived
value, anything else is rejected. ``runtime_version`` must be explicit and is stored
unchanged. Nothing is built, activated or granted: this is persistence only.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from pydantic import ValidationError

from src.base.operations.v2.admission import sha256_hex
from src.domains.agentverse.entities.agent import Agent
from src.domains.agentverse.tools.store_tool.models import (
    InvalidStorePayload,
    StoreConflict,
    StoreCreateResult,
    StoreIdempotencyConflict,
    StoreOwnershipViolation,
    StoreResourceType,
    StoreWriteContext,
)

STORE_RECORD_VERSION = 1
_SERVER_FIELDS = ("id", "ownership", "tenant_id", "creator", "exposure")
# Runtime objects and catalog flags that must never arrive through a stored definition.
_REJECTED_FIELDS = frozenset({"commbridge", "_id", "genesis_local_seed", "tenant_scoped"})


class AgentDefinitionPersistence(Protocol):
    async def ensure_indexes(self) -> None: ...

    async def insert_once(self, document: dict[str, Any]) -> tuple[dict[str, Any], bool]: ...

    async def find_by_id(self, agent_id: str) -> dict[str, Any] | None: ...


def idempotency_scope(context: StoreWriteContext, key: str) -> str:
    provenance = context.provenance
    return scope_digest(
        tenant_id=provenance.tenant_id,
        subject_id=provenance.subject_id,
        principal_id=provenance.principal_id,
        key=key,
    )


def scope_digest(*, tenant_id: str, subject_id: str, principal_id: str, key: str) -> str:
    """Store's write scope; independent of session and origin, so retries converge."""
    return sha256_hex(
        {
            "resource_type": "agent",
            "tenant": tenant_id,
            "subject": subject_id,
            "principal": principal_id,
            "key": key,
        }
    )


class AgentDefinitionStore:
    resource_type: StoreResourceType = "agent"

    def __init__(
        self,
        *,
        persistence: AgentDefinitionPersistence,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.persistence = persistence
        self._clock = clock

    async def ensure_indexes(self) -> None:
        await self.persistence.ensure_indexes()

    async def create(
        self, payload: dict[str, Any], *, idempotency_key: str, context: StoreWriteContext
    ) -> StoreCreateResult:
        if context.ownership != "tenant":
            # No reviewed system bootstrap authority exists yet: fail closed.
            raise StoreOwnershipViolation("System-owned agent creation is not enabled")
        scope = idempotency_scope(context, idempotency_key)
        provisioned = context.provisioned
        agent_id = provisioned.agent_id if provisioned else str(UUID(scope[:32]))
        agent = self._definition(payload, context, agent_id=agent_id)
        definition = agent.model_dump(mode="json")
        digest = sha256_hex({"resource_type": "agent", "definition": definition})
        now = self._clock()
        document = {
            **agent.model_dump(),
            # Same timestamp fields as DBService.store_agent + MongoDBRepository.create.
            "created_at": now,
            "updated_at": now,
            "created": now,
            "modified": now,
            "store": {
                "version": STORE_RECORD_VERSION,
                "operation": "create",
                "resource_type": "agent",
                "idempotency_scope": scope,
                "request_digest": digest,
                "authorization": context.provenance.model_dump(mode="json"),
                "stored_at": now,
            },
        }
        if provisioned is not None:
            # A pinned ID is not scope-derived and coll_agents has no unique ``id``
            # index: refuse an identity already held outside this scope.
            existing = await self.persistence.find_by_id(agent_id)
            if (
                existing is not None
                and (existing.get("store") or {}).get("idempotency_scope") != scope
            ):
                raise StoreConflict("Agent identity already exists")
        # Revalidate live authority at the write boundary (revocation/policy change).
        await context.verify()
        stored, created = await self.persistence.insert_once(document)
        record = stored.get("store") or {}
        if record.get("request_digest") != digest:
            raise StoreIdempotencyConflict("Idempotency key already stored a different agent")
        return StoreCreateResult(
            resource_type="agent",
            resource_id=stored["id"],
            created=created,
            request_digest=digest,
        )

    @staticmethod
    def _definition(payload: dict[str, Any], context: StoreWriteContext, *, agent_id: str) -> Agent:
        if not isinstance(payload, dict):
            raise InvalidStorePayload("Agent definition must be an object")
        unknown = payload.keys() - Agent.model_fields.keys()
        if unknown or _REJECTED_FIELDS & payload.keys():
            raise InvalidStorePayload("Agent definition contains unsupported fields")
        if payload.get("runtime_version") not in ("v1", "v2"):
            # Never default: an omitted version would silently become V1.
            raise InvalidStorePayload("Agent definition requires an explicit runtime_version")
        provenance = context.provenance
        derived = {
            "id": agent_id,
            "ownership": "tenant",
            "tenant_id": provenance.tenant_id,
            "creator": provenance.subject_id,
            "exposure": "public",
        }
        for field in _SERVER_FIELDS:
            if field in payload and payload[field] != derived[field]:
                raise StoreOwnershipViolation(f"Agent {field} is managed by the server")
        pinned = context.provisioned
        if pinned is not None:
            if (pinned.tenant_id, pinned.subject_id) != (
                provenance.tenant_id,
                provenance.subject_id,
            ):
                raise StoreOwnershipViolation("Provisioned identity does not match authority")
            if (payload.get("name"), payload.get("type"), payload["runtime_version"]) != (
                pinned.name,
                pinned.type,
                pinned.runtime_version,
            ):
                raise InvalidStorePayload(
                    "Agent definition does not match its provisioned identity"
                )
        try:
            return Agent.model_validate({**payload, **derived})
        except ValidationError as exc:
            fields = sorted({".".join(map(str, error["loc"])) for error in exc.errors()})
            raise InvalidStorePayload(f"Invalid agent definition: {', '.join(fields)}") from exc
