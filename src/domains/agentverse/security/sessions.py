"""Durable, terminal Genesis sessions. All provisioning is trusted control plane.

Records and the expiry/retry index share a Redis slot and never receive a TTL.
The index is a recoverable work queue, not the expiry enforcement boundary.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Protocol

from src.base.events.v2.contracts import EventPermissionError
from src.base.services.jwt_service import JWTService
from src.domains.agentverse.agents.lifecycle.agent_principal_resolver import VerifiedAgentSession
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey

logger = logging.getLogger(__name__)

_CREATE = """
if redis.call('HEXISTS', KEYS[1], ARGV[1]) == 1 then return 0 end
if redis.call('HEXISTS', KEYS[1], 'membership:' .. ARGV[4]) == 1 then return 0 end
redis.call('HSET', KEYS[1], ARGV[1], ARGV[2])
redis.call('ZADD', KEYS[2], ARGV[3], ARGV[1])
return 1
"""
_INTENT = """
local raw = redis.call('HGET', KEYS[1], ARGV[1])
if not raw then return false end
local r = cjson.decode(raw)
if r.status == 'active' then
  r.status = 'pending'
  raw = cjson.encode(r)
  redis.call('HSET', KEYS[1], ARGV[1], raw)
end
if r.status == 'pending' then redis.call('ZADD', KEYS[2], 0, ARGV[1]) end
return raw
"""
_COMPLETE = """
local raw = redis.call('HGET', KEYS[1], ARGV[1])
if not raw then return 0 end
local r = cjson.decode(raw)
if r.status ~= 'pending' and r.status ~= 'revoked' then return 0 end
r.status = 'revoked'
redis.call('HSET', KEYS[1], ARGV[1], cjson.encode(r))
redis.call('ZREM', KEYS[2], ARGV[1])
return 1
"""


class SessionRedisRepository(Protocol):
    """The existing RedisRepository.eval contract, including lazy DI bindings."""

    async def eval(self, script: str, keys: list[str], args: list[Any]) -> Any: ...


@dataclass(frozen=True)
class _Caller:
    tenant: str
    user: str
    session: str
    fingerprint: str
    expires: float


@dataclass
class _Binding:
    caller: _Caller
    active: bool = True


class RedisAgentSessionAuthority:
    """Server-approved membership and allowed agents, bound to a verified JWT.

    Tokens must contain signed tid, sid, sub and exp claims. oid, when present,
    is the owner identity used by the existing authentication layer. sid alone
    never authenticates. Background execution checks durable records directly;
    background activation requires a separate explicit lease adapter.
    """

    def __init__(
        self,
        *,
        repository: SessionRedisRepository,
        jwt_service: JWTService,
        prefix: str = "caos:{genesis-sessions}:v1",
        timeout: float = 1.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if timeout <= 0:
            raise ValueError("Session timeout must be positive")
        self._repo = repository
        self._jwt = jwt_service
        self._keys = [prefix + ":records", prefix + ":due"]
        self._timeout = timeout
        self._clock = clock
        self._caller: ContextVar[_Binding | None] = ContextVar("genesis_caller", default=None)
        self._handler: Callable[..., Awaitable[bool]] | None = None
        self._worker: asyncio.Task[None] | None = None

    def bind_revocation_handler(self, handler: Callable[..., Awaitable[bool]]) -> None:
        if self._handler is not None and self._handler != handler:
            raise ValueError("Session authority already bound to UTU")
        self._handler = handler

    @staticmethod
    def _field(session_id: str) -> str:
        return hashlib.sha256(session_id.encode()).hexdigest()

    async def _eval(self, script: str, args: list[Any]) -> Any:
        try:
            async with asyncio.timeout(self._timeout):
                return await self._repo.eval(script, keys=self._keys, args=args)
        except Exception as exc:
            raise EventPermissionError("Genesis session authority unavailable") from exc

    async def _verify(self, token: str) -> _Caller:
        claims = await self._jwt.verify_token(token)
        if not claims:
            raise EventPermissionError("Verified Genesis token required")
        values = [claims.get("tid"), claims.get("oid") or claims.get("sub"), claims.get("sid")]
        if any(not isinstance(v, str) or not v.strip() for v in [*values, claims.get("sub")]):
            raise EventPermissionError("Genesis token requires tenant, subject and session")
        expiry = claims.get("exp")
        if isinstance(expiry, bool) or not isinstance(expiry, int | float):
            raise EventPermissionError("Genesis token requires expiry")
        if not math.isfinite(expiry) or expiry <= self._clock():
            raise EventPermissionError("Genesis token expired")
        return _Caller(*values, hashlib.sha256(token.encode()).hexdigest(), float(expiry))

    async def provision(
        self,
        token: str,
        *,
        tenant_id: str,
        user_id: str,
        allowed_agent_ids: frozenset[str],
    ) -> str:
        """Trusted membership attestation; never expose as a caller-written route.

        Reuse/overwrite is denied, including after revocation. New logins need a
        fresh signed sid. The caller attests current membership and agent access.
        """
        caller = await self._verify(token)
        if (caller.tenant, caller.user) != (tenant_id, user_id):
            raise EventPermissionError("Session provisioning identity mismatch")
        if any(not isinstance(v, str) or not v.strip() for v in allowed_agent_ids):
            raise ValueError("Allowed agent IDs must be nonempty strings")
        record = {
            "tenant": caller.tenant,
            "user": caller.user,
            "session": caller.session,
            "fingerprint": caller.fingerprint,
            "expires": caller.expires,
            "agents": {agent: True for agent in allowed_agent_ids},
            "status": "active",
        }
        if (
            await self._eval(
                _CREATE,
                [
                    self._field(caller.session),
                    json.dumps(record),
                    caller.expires,
                    self._field(json.dumps([tenant_id, user_id])),
                ],
            )
            != 1
        ):
            raise EventPermissionError("Session ID already provisioned")
        return caller.session

    async def _record(self, session_id: str) -> dict[str, Any]:
        raw = await self._eval(
            "return redis.call('HGET', KEYS[1], ARGV[1])", [self._field(session_id)]
        )
        try:
            record = json.loads(raw) if raw else None
            if not isinstance(record, dict) or record.get("session") != session_id:
                raise ValueError("Missing session")
            if (
                any(
                    not isinstance(record.get(k), str) or not record[k]
                    for k in ("tenant", "user", "session", "fingerprint")
                )
                or record.get("status") not in {"active", "pending", "revoked"}
                or not isinstance(record.get("expires"), int | float)
                or isinstance(record["expires"], bool)
                or not math.isfinite(record["expires"])
                or not isinstance(record.get("agents"), dict)
                or any(
                    not isinstance(v, str) or not v or enabled is not True
                    for v, enabled in record["agents"].items()
                )
            ):
                raise ValueError("Invalid session record")
            return record
        except (ValueError, TypeError) as exc:
            raise EventPermissionError("Unknown Genesis session") from exc

    async def _active(self, session_id: str) -> dict[str, Any]:
        record = await self._record(session_id)
        if record.get("status") != "active" or record["expires"] <= self._clock():
            await self.invalidate(session_id)
            raise EventPermissionError("Genesis session invalidated or expired")
        if self._handler is None:
            raise EventPermissionError("Genesis session revocation handler not bound")
        return record

    async def _bound_record(self, caller: _Caller) -> dict[str, Any]:
        record = await self._record(caller.session)
        if (record["tenant"], record["user"], record["fingerprint"], record["expires"]) != (
            caller.tenant,
            caller.user,
            caller.fingerprint,
            caller.expires,
        ):
            raise EventPermissionError("Genesis session caller mismatch")
        if caller.expires <= self._clock():
            await self.invalidate(caller.session)
            raise EventPermissionError("Genesis token expired")
        return record

    @asynccontextmanager
    async def authenticated(
        self, token: str, *, for_logout: bool = False
    ) -> AsyncIterator[VerifiedAgentSession]:
        caller = await self._verify(token)
        await self._bound_record(caller)
        binding = _Binding(caller)
        marker = self._caller.set(binding)
        try:
            if for_logout:
                yield VerifiedAgentSession(caller.tenant, caller.user, caller.session)
            else:
                yield await self.get_session(caller.session)
        finally:
            binding.active = False
            self._caller.reset(marker)

    async def get_session(self, session_id: str) -> VerifiedAgentSession:
        binding = self._caller.get()
        caller = binding.caller if binding is not None and binding.active else None
        if caller is None or caller.session != session_id:
            raise EventPermissionError("Authenticated session context required")
        await self._bound_record(caller)
        record = await self._active(session_id)
        return VerifiedAgentSession(
            record["tenant"],
            record["user"],
            session_id,
            frozenset(record["agents"]),
            True,
        )

    async def check_execution_session(self, key: AgentInstanceKey) -> None:
        """UTU permit hook; no HTTP context required for existing runtimes."""
        record = await self._active(key.session_id)
        if (record["tenant"], record["user"]) != (
            key.tenant_id,
            key.user_id,
        ) or key.agent_id not in record["agents"]:
            raise EventPermissionError("Genesis execution session mismatch")

    async def logout(self) -> None:
        binding = self._caller.get()
        caller = binding.caller if binding is not None and binding.active else None
        if caller is None:
            raise EventPermissionError("Authenticated session context required")
        await self._bound_record(caller)
        await self.invalidate(caller.session)

    async def invalidate(self, session_id: str) -> None:
        """Trusted invalidation: intent first, UTU second, completion last.

        A timeout is indeterminate; retry the same ID. No successful response is
        returned until UTU acknowledges durable revocation (False is idempotent).
        """
        raw = await self._eval(_INTENT, [self._field(session_id)])
        if not raw:
            raise EventPermissionError("Unknown Genesis session")
        record = json.loads(raw)
        if record["status"] == "revoked":
            return
        if self._handler is None:
            raise EventPermissionError("Genesis session revocation handler not bound")
        result = await self._handler(record["tenant"], session_id=record["session"])
        if type(result) is not bool:
            raise EventPermissionError("UTU did not acknowledge session revocation")
        if await self._eval(_COMPLETE, [self._field(session_id)]) != 1:
            raise EventPermissionError("Session invalidation completion failed")

    async def remove_membership(self, tenant_id: str, user_id: str) -> None:
        """Trusted membership writer must serialize with provisioning.

        Persist a membership fence atomically with all matching session intents,
        preventing concurrent provisioning from reopening removed membership.
        """
        scope = self._field(json.dumps([tenant_id, user_id]))
        await self._eval(
            """
redis.call('HSET', KEYS[1], 'membership:' .. ARGV[1], 'revoked')
local all = redis.call('HGETALL', KEYS[1])
for i = 1, #all, 2 do
  if string.sub(all[i], 1, 11) ~= 'membership:' then
    local r = cjson.decode(all[i+1])
    if r.tenant == ARGV[2] and r.user == ARGV[3] and r.status == 'active' then
      r.status = 'pending'
      redis.call('HSET', KEYS[1], all[i], cjson.encode(r))
      redis.call('ZADD', KEYS[2], 0, all[i])
    end
  end
end
return 1
""",
            [scope, tenant_id, user_id],
        )
        await self.process_due()
        # Verify matching intents completed even if another worker took work.
        raws = await self._eval("return redis.call('HVALS', KEYS[1])", [])
        for raw in raws:
            if raw in (b"revoked", "revoked"):
                continue
            record = json.loads(raw)
            if (record["tenant"], record["user"]) == (tenant_id, user_id):
                await self.invalidate(record["session"])

    async def process_due(self) -> None:
        fields = await self._eval(
            "return redis.call('ZRANGEBYSCORE', KEYS[2], '-inf', ARGV[1], 'LIMIT', 0, 100)",
            [self._clock()],
        )
        failures = []
        for field in fields:
            try:
                raw = await self._eval("return redis.call('HGET', KEYS[1], ARGV[1])", [field])
                if not raw:
                    raise EventPermissionError("Missing durable expiry record")
                await self.invalidate(json.loads(raw)["session"])
            except Exception as exc:
                failures.append(exc)
                # Back off this item so an unprovisioned tenant cannot starve
                # other tenants' expiry work in the bounded batch.
                await self._eval(
                    """
local raw = redis.call('HGET', KEYS[1], ARGV[1])
if raw and cjson.decode(raw).status == 'pending' then
  redis.call('ZADD', KEYS[2], ARGV[2], ARGV[1])
end
return 1
""",
                    [field, self._clock() + 1],
                )
        if failures:
            raise EventPermissionError("Session expiry/revocation pending retry") from failures[0]

    async def start(self) -> None:
        if self._handler is None:
            raise EventPermissionError("Bind UTU before starting sessions")
        if self._worker is not None:
            return

        async def run() -> None:
            failed = False
            while True:
                try:
                    await self.process_due()
                    failed = False
                except Exception:
                    # Durable intents stay indexed; admissions/permits fail closed.
                    if not failed:
                        logger.exception("Genesis session replay failed; durable work will retry")
                    failed = True
                await asyncio.sleep(0.5)

        self._worker = asyncio.create_task(run(), name="genesis-session-revocations")

    async def close(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
            self._worker = None
