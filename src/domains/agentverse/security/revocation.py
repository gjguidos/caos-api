"""Redis 6 compatible, tenant-slot atomic revocation state. No positive cache."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from src.domains.agentverse.ncsc.authorization import SynapticAuthorization
from uuid import uuid4

from src.base.events.v2.contracts import EventPermissionError
from src.base.repositories.redis_repository import RedisRepository
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey
from src.domains.agentverse.security.capability_scope import CapabilityBinding, CapabilityScope

# One hash per tenant also works with Redis Cluster's single-slot Lua restriction.
_READ = """
local p = redis.call('HGET', KEYS[1], 'policy')
local t = redis.call('HGET', KEYS[1], 'tenant')
if not p or not t or t == 'revoked' then return {} end
local a = redis.call('HGET', KEYS[1], ARGV[1]) or '0'
local s = redis.call('HGET', KEYS[1], ARGV[2]) or '0'
if a == 'revoked' or s == 'revoked' then return {} end
return {p, t, a, s}
"""
_POLICY = """
local old = redis.call('HGET', KEYS[1], 'policy')
if (old or '') ~= ARGV[1] then return 0 end
redis.call('HSET', KEYS[1], 'policy', ARGV[2])
redis.call('HSETNX', KEYS[1], 'tenant', ARGV[3])
return 1
"""
_REVOKE = """
if not redis.call('HGET', KEYS[1], 'policy') then return -1 end
if redis.call('HGET', KEYS[1], ARGV[1]) == 'revoked' then return 0 end
redis.call('HSET', KEYS[1], ARGV[1], 'revoked')
return 1
"""


def policy_version(policy: SynapticAuthorization) -> str:
    # The digest is both the deployment version and the exact policy identity.
    value = [
        sorted((list(k), sorted(v.invoke)) for k, v in policy._grants.items()),
        sorted(
            (v.source, v.target, v.tenant_id or "", v.principal_id or "")
            for v in policy._trusted_lines
        ),
    ]
    return hashlib.sha256(json.dumps(value).encode()).hexdigest()


@dataclass(frozen=True)
class ExecutionPermit:
    key: AgentInstanceKey
    versions: tuple[str, ...]
    # Set by UTU from the verified session record and rechecked against it.
    origin: Literal["session", "bootstrap"] = "session"
    scope: CapabilityScope | None = None
    # Scoped child permits retain their path even when trusted code rebinds them.
    binding_chain: tuple[CapabilityBinding, ...] = ()

    def __post_init__(self) -> None:
        if self.scope is not None and type(self.scope) is not CapabilityScope:
            raise ValueError("Unknown capability restrictions")
        if (
            type(self.binding_chain) is not tuple
            or len(self.binding_chain) > 64
            or any(type(binding) is not CapabilityBinding for binding in self.binding_chain)
            or (self.binding_chain and self.scope is None)
        ):
            raise ValueError("Invalid scoped invocation lineage")
        if (
            self.scope
            and self.binding_chain
            and (
                self.binding_chain[0] != self.scope.entry
                or any(binding not in self.scope.bindings for binding in self.binding_chain)
            )
        ):
            raise ValueError("Invocation lineage exceeds capability scope")


class RevocationStore(Protocol):
    async def current_policy(self, tenant_id: str) -> str | None: ...
    async def read(self, key: AgentInstanceKey) -> tuple[str, ...]: ...
    async def revoke(
        self, tenant_id: str, *, agent_id: str | None = None, session_id: str | None = None
    ) -> bool: ...
    async def install_policy(
        self, tenant_id: str, *, expected: str | None, version: str
    ) -> None: ...


class RedisRevocationStore:
    """Redis must be durable, shared and non-evicting. Missing state denies.

    Policy provisioning is explicit CAS, never performed by client issuance.
    The random tenant incarnation fences old clients after recovery/reprovision.
    Revoked agent/session IDs cannot be reused; issue a new ID for new authority.
    """

    def __init__(self, *, repository: RedisRepository, timeout: float = 1.0) -> None:
        if timeout <= 0:
            raise ValueError("Revocation timeout must be positive")
        self._repo = repository
        self._timeout = timeout

    @staticmethod
    def _key(tenant: str) -> str:
        return "caos:utu:v1:{" + hashlib.sha256(tenant.encode()).hexdigest() + "}"

    @staticmethod
    def _field(kind: str, value: str) -> str:
        return kind + ":" + hashlib.sha256(value.encode()).hexdigest()

    async def _eval(self, script: str, tenant: str, args: list[str]) -> Any:
        try:
            async with asyncio.timeout(self._timeout):
                return await self._repo.eval(script, keys=[self._key(tenant)], args=args)
        except Exception as exc:
            raise EventPermissionError("UTU revocation authority unavailable") from exc

    async def read(self, key: AgentInstanceKey) -> tuple[str, ...]:
        result = await self._eval(
            _READ,
            key.tenant_id,
            [self._field("agent", key.agent_id), self._field("session", key.session_id)],
        )
        if not isinstance(result, (list, tuple)) or len(result) != 4:
            raise EventPermissionError("UTU scope revoked or unprovisioned")
        return tuple(v.decode() if isinstance(v, bytes) else str(v) for v in result)

    async def current_policy(self, tenant_id: str) -> str | None:
        value = await self._eval("return redis.call('HGET', KEYS[1], 'policy')", tenant_id, [])
        return value.decode() if isinstance(value, bytes) else str(value) if value else None

    async def install_policy(self, tenant_id: str, *, expected: str | None, version: str) -> None:
        if await self._eval(_POLICY, tenant_id, [expected or "", version, uuid4().hex]) != 1:
            raise EventPermissionError("UTU policy version conflict")

    async def revoke(
        self, tenant_id: str, *, agent_id: str | None = None, session_id: str | None = None
    ) -> bool:
        if agent_id is not None and session_id is not None:
            raise ValueError("Revoke exactly one scope")
        field = (
            self._field("agent", agent_id)
            if agent_id is not None
            else self._field("session", session_id)
            if session_id is not None
            else "tenant"
        )
        result = await self._eval(_REVOKE, tenant_id, [field])
        if result not in (0, 1):
            raise EventPermissionError("UTU tenant not provisioned")
        return bool(result)
