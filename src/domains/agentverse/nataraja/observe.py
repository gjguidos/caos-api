"""Read-only observation of the actual environment.

The ports below expose read operations only (no insert/update/delete/eval/flush), so
inspection cannot change Mongo or Redis even by mistake. Agent prompts, DNA rules,
credentials and connection secrets are never collected.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from bson.codec_options import CodecOptions

from src.base.operations.v2.admission import sha256_hex
from src.base.operations.v2.mongo_managed_repository import managed_from_document
from src.base.operations.v2.mongo_repository import COLLECTION_NAME as OPERATIONS_COLLECTION
from src.domains.agentverse.audit.models import LOGS_COLLECTION
from src.domains.agentverse.audit.repository import record_from_document
from src.domains.agentverse.security.revocation import RedisRevocationStore, policy_version

AGENTS_COLLECTION = "coll_agents"
PRESENCE_PATTERN = "caos:agent:presence:v2:*"
_CODEC: CodecOptions[dict[str, Any]] = CodecOptions(tz_aware=True, tzinfo=UTC)
_AGENT_FIELDS = (
    "id",
    "name",
    "type",
    "role_name",
    "personality_key",
    "runtime_version",
    "tenant_id",
    "creator",
    "ownership",
    "exposure",
    "gui_components",
)


class ReadOnlyMongo(Protocol):
    async def find(
        self, collection: str, query: dict[str, Any], projection: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]: ...

    async def collections(self) -> list[str]: ...


class ReadOnlyRedis(Protocol):
    async def hget(self, key: str, field: str) -> str | None: ...

    async def count(self, pattern: str) -> int: ...


class MotorReadOnlyMongo:
    """Only ``find`` and ``list_collection_names`` reach the database."""

    def __init__(self, database: Any, *, limit: int = 1000) -> None:
        self._database = database
        self._limit = limit

    async def find(
        self, collection: str, query: dict[str, Any], projection: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        handle = self._database.get_collection(collection, codec_options=_CODEC)
        return await handle.find(query, projection).to_list(length=self._limit)

    async def collections(self) -> list[str]:
        return sorted(await self._database.list_collection_names())


class AsyncReadOnlyRedis:
    """Only ``HGET`` and cursor-bounded ``SCAN`` reach Redis."""

    def __init__(self, client: Any) -> None:
        self._client = client

    async def hget(self, key: str, field: str) -> str | None:
        value = await self._client.hget(key, field)
        return value.decode() if isinstance(value, bytes) else value

    async def count(self, pattern: str) -> int:
        total = 0
        async for _ in self._client.scan_iter(match=pattern, count=500):
            total += 1
        return total


def redact_target(url: str | None) -> dict[str, Any]:
    """Host, port and database only: credentials and query options are dropped."""
    if not url:
        return {"configured": False}
    parts = urlsplit(url)
    return {
        "configured": True,
        "scheme": parts.scheme,
        "host": parts.hostname,
        "port": parts.port,
        "database": parts.path.lstrip("/") or None,
    }


@dataclass
class Observation:
    environment: dict[str, Any]
    targets: dict[str, Any]
    policy: Any = None  # GenesisSecurityBindings parsed by production rules
    policy_error: str | None = None
    policy_file_digest: str | None = None
    policy_version: str | None = None
    installed_policy_version: str | None = None
    roles: frozenset[str] = frozenset()
    souls: frozenset[str] = frozenset()
    agent_types: frozenset[str] = frozenset()
    services: dict[str, bool] = field(default_factory=dict)
    agents: list[dict[str, Any]] = field(default_factory=list)
    operations: list[dict[str, Any]] = field(default_factory=list)
    corrupt_operations: int = 0
    evidence: list[dict[str, Any]] = field(default_factory=list)
    corrupt_evidence: int = 0
    collections: list[str] = field(default_factory=list)
    presence_count: int | None = None
    # What the running API process reports about itself; None = unavailable.
    runtime: dict[str, Any] | None = None
    unreachable: list[str] = field(default_factory=list)


def environment_identity() -> dict[str, Any]:
    policy_file = os.environ.get("GENESIS_SECURITY_POLICY_FILE")
    return {
        "environment": os.environ.get("ENVIRONMENT"),
        "auth_mode": os.environ.get("AUTH_MODE"),
        "genesis_local_enabled": os.environ.get("GENESIS_LOCAL_ENABLED") == "true",
        "genesis_sessions_enabled": os.environ.get("GENESIS_SESSIONS_ENABLED") == "true",
        "policy_file": Path(policy_file).name if policy_file else None,
    }


def parse_policy(container: Any) -> tuple[Any, str | None, str | None]:
    """The policy exactly as startup parses and validates it (no Redis, no writes)."""
    from src.domains.agentverse.dependencies.genesis_bootstrap import build_genesis_security

    path = os.environ.get("GENESIS_SECURITY_POLICY_FILE")
    digest = sha256_hex(Path(path).read_text()) if path and Path(path).is_file() else None
    try:
        bindings = build_genesis_security(container).get("genesis_security")
    except (ValueError, TypeError, KeyError, OSError) as exc:
        return None, f"{type(exc).__name__}: policy rejected", digest
    if bindings is None:
        return None, "Genesis sessions are disabled", digest
    return bindings, None, digest


def registry_keys() -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    from src.domains.agentverse import registries

    return (
        frozenset(registries.role_registry_instance.keys()),
        frozenset(registries.personality_registry_instance.keys()),
        frozenset(registries.agent_registry_instance.keys()),
    )


def declared_services() -> dict[str, bool]:
    """Trusted runtime services are DI providers, not principals: check they are wired."""
    from src.domains.agentverse.dependencies.di_container import AgentverseContainer

    providers = set(AgentverseContainer.providers)
    return {
        "UTU": "utu_authority" in providers,
        "CPBX": "synaptic_exchange" in providers and "synaptic_authorization" in providers,
        "Khepri": "khepri" in providers,
        "Seshat": "caos_log_service" in providers,
    }


async def observe(
    *,
    tenant_id: str,
    mongo: ReadOnlyMongo | None,
    redis: ReadOnlyRedis | None,
    container: Any,
    targets: dict[str, Any],
    runtime: Any = None,
) -> Observation:
    observation = Observation(environment=environment_identity(), targets=targets)
    if runtime is not None:
        try:
            observation.runtime = await runtime()
        except Exception:  # noqa: BLE001 - an unavailable runtime is reported, not assumed
            observation.runtime = None
    observation.policy, observation.policy_error, observation.policy_file_digest = parse_policy(
        container
    )
    if observation.policy is not None:
        observation.policy_version = policy_version(observation.policy.authorization)
    observation.roles, observation.souls, observation.agent_types = registry_keys()
    observation.services = declared_services()

    if redis is None:
        observation.unreachable.append("redis")
    else:
        try:
            installed = await redis.hget(RedisRevocationStore._key(tenant_id), "policy")
            observation.installed_policy_version = installed.split(":")[-1] if installed else None
            observation.presence_count = await redis.count(PRESENCE_PATTERN)
        except Exception:  # noqa: BLE001 - any failure is reported, never retried as a write
            observation.unreachable.append("redis")

    if mongo is None:
        observation.unreachable.append("mongo")
        return observation
    try:
        observation.collections = await mongo.collections()
        projection = {"_id": 0, **{name: 1 for name in _AGENT_FIELDS}, "store": 1}
        for document in await mongo.find(AGENTS_COLLECTION, {"tenant_id": tenant_id}, projection):
            store = document.pop("store", None) or {}
            authorization = store.get("authorization") or {}
            document["store"] = (
                {
                    "operation": store.get("operation"),
                    "origin": authorization.get("origin", "session"),
                    "principal_id": authorization.get("principal_id"),
                    "subject_id": authorization.get("subject_id"),
                    "scoped": bool(store.get("idempotency_scope")),
                }
                if store
                else None
            )
            observation.agents.append(document)
        for document in await mongo.find(OPERATIONS_COLLECTION, {}):
            try:
                operation = managed_from_document(document).snapshot
            except Exception:  # noqa: BLE001 - corruption is a finding
                observation.corrupt_operations += 1
                continue
            observation.operations.append(
                {
                    "operation_id": str(operation.operation_id),
                    "kind": operation.kind,
                    "state": str(operation.state),
                    "attempts": len(operation.attempts),
                }
            )
        for document in await mongo.find(LOGS_COLLECTION, {"tenant_id": tenant_id}):
            try:
                record = record_from_document(document)
            except Exception:  # noqa: BLE001 - corruption is a finding
                observation.corrupt_evidence += 1
                continue
            observation.evidence.append(
                {
                    "event_id": record.event_id,
                    "event_type": record.event_type,
                    "outcome": record.outcome,
                    "resource_id": record.subject.resource_id,
                    "operation_id": str(record.operation_id) if record.operation_id else None,
                    "correlation_id": record.correlation_id,
                    "causation_id": record.causation_id,
                    "principal_id": record.actor.principal_id,
                    "origin": record.authorization.origin,
                }
            )
    except Exception:  # noqa: BLE001 - unreachable store is reported, not hidden
        observation.unreachable.append("mongo")
    return observation
