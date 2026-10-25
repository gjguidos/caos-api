
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from uuid import uuid4

from src.base.repositories.redis_repository import RedisRepository
from src.domains.agentverse.runtime.instances_v2 import (
    AgentInstanceKey,
)

# Atomic renewal: never extend another owner's lease.
_RENEW_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return 0
"""

# Atomic release: never delete another owner's lease.
_RELEASE_SCRIPT = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


@dataclass(frozen=True, slots=True)
class AgentLease:
    runtime_id: str
    token: str

    def __post_init__(self) -> None:
        if not self.runtime_id or not self.token:
            raise ValueError("Runtime ID and lease token are required")

    def serialize(self) -> str:
        return json.dumps(
            {
                "runtime_id": self.runtime_id,
                "token": self.token,
            },
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def deserialize(cls, raw: str) -> AgentLease:
        return cls(**json.loads(raw))


class AgentPresenceStore:
    """
    Redis-backed ownership leases for CAOS agent runtimes.

    Shared by Khepri and Anubis.

    The store does not construct agents, manage Python instances,
    or decide lifecycle state.
    """

    def __init__(
        self,
        *,
        repository: RedisRepository,
        lease_ttl: int = 60,
    ) -> None:
        if lease_ttl <= 0:
            raise ValueError("Lease TTL must be positive")

        self._repo = repository
        self._ttl = lease_ttl

    @staticmethod
    def _key(key: AgentInstanceKey) -> str:
        identity = json.dumps(
            [
                key.tenant_id,
                key.user_id,
                key.agent_id,
                key.session_id,
            ],
            separators=(",", ":"),
        )

        digest = hashlib.sha256(identity.encode()).hexdigest()

        return f"caos:agent:presence:v2:{digest}"

    async def claim(
        self,
        key: AgentInstanceKey,
        *,
        runtime_id: str,
    ) -> AgentLease | None:
        """
        Claim an unowned or expired agent-session lease.

        Returns None when another lease currently exists.
        """
        lease = AgentLease(
            runtime_id=runtime_id,
            token=uuid4().hex,
        )

        claimed = await self._repo.set(
            self._key(key),
            lease.serialize(),
            expiration=self._ttl,
            nx=True,
        )

        return lease if claimed else None

    async def get_owner(
        self,
        key: AgentInstanceKey,
    ) -> AgentLease | None:
        raw = await self._repo.get(self._key(key))

        if raw is None:
            return None

        return AgentLease.deserialize(raw)

    async def renew(
        self,
        key: AgentInstanceKey,
        *,
        lease: AgentLease,
    ) -> bool:
        result = await self._repo.eval(
            _RENEW_SCRIPT,
            keys=[self._key(key)],
            args=[
                lease.serialize(),
                self._ttl,
            ],
        )

        return bool(result)


    async def release(
        self,
        key: AgentInstanceKey,
        *,
        lease: AgentLease,
    ) -> bool:
        result = await self._repo.eval(
            _RELEASE_SCRIPT,
            keys=[self._key(key)],
            args=[lease.serialize()],
        )

        return bool(result)

    async def is_owner(
        self,
        key: AgentInstanceKey,
        *,
        lease: AgentLease,
    ) -> bool:
        owner = await self.get_owner(key)
        return owner == lease
