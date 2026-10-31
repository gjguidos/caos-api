"""Redis-backed Genesis conversation documents and bounded durable turn journal.

Uses the same configured repository as durable Genesis sessions. Documents, journal and
transcript have no TTL or eviction. CONV-REC-0.1: the in-flight turn is an owned, renewable
lease (``caos.communication.turn`` v2) with a monotonic per-conversation fence and a hard
ceiling (maximum turn duration plus a bounded finalization grace). Journal and transcript
commits require the live lease's exact identity, fence and owner, so an expired worker can
never overwrite a newer attempt. An abandoned attempt is reconciled lazily, on the next
admission or replay in that conversation, into an immutable ``interrupted`` terminal result
whose outcome is unknown: expiry is never proof that side effects did not happen. Nothing
is re-executed, including model inference; a retry is a new client event.
"""

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.entities.conversation_v2 import ClientEventV2, ConversationV2
from src.domains.agentverse.entities.history_v2 import ConversationMessageV2
from src.domains.agentverse.security.sessions import SessionRedisRepository

# Redis server time inside every script; process clocks never decide lease ownership.
_COMMON = """
local function now_ms()
  local t = redis.call('TIME')
  return tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
end
local function lease(raw)
  if not raw then return nil end
  local ok, turn = pcall(cjson.decode, raw)
  if ok and type(turn) == 'table' and turn.fence then return turn end
  return nil
end
"""

# KEYS: document, events, turn, fence. ARGV: identity, digest, entry, owner, lease_ms,
# window_ms, conversation_id.
_BEGIN = (
    _COMMON
    + """
local function settle(identity, entry)
  local request = entry.request or {}
  entry.response = cjson.encode({
    event_id = 'interrupted-' .. tostring(entry.fence or 0),
    conversation_id = ARGV[7],
    correlation_id = request.event_id,
    type = 'event.error',
    code = 'interrupted',
    outcome = 'unknown',
  })
  entry.settled_by = 'recovery'
  redis.call('HSET', KEYS[2], identity, cjson.encode(entry))
  return entry.response
end
local function settle_identity(identity)
  local raw = redis.call('HGET', KEYS[2], identity)
  if raw then
    local entry = cjson.decode(raw)
    if not entry.response then settle(identity, entry) end
  end
end
local raw = redis.call('GET', KEYS[3])
local turn = lease(raw)
if raw and not turn then
  -- A pre-lease lock (no TTL, no fence): no worker can renew it. Recover it.
  redis.call('DEL', KEYS[3])
  settle_identity(raw)
end
local previous = redis.call('HGET', KEYS[2], ARGV[1])
if previous then
  local r = cjson.decode(previous)
  if r.digest ~= ARGV[2] then return {'conflict'} end
  if r.response then return {'replay', r.response} end
  if turn and turn.identity == ARGV[1] and turn.fence == r.fence then return {'pending'} end
  return {'replay', settle(ARGV[1], r)}
end
if turn then return {'busy'} end
local last = redis.call('HGET', KEYS[4], 'identity')
if last then settle_identity(last) end
if redis.call('HLEN', KEYS[2]) >= 256 then return {'full'} end
local fence = redis.call('HINCRBY', KEYS[4], 'seq', 1)
redis.call('HSET', KEYS[4], 'identity', ARGV[1])
local entry = cjson.decode(ARGV[3])
entry.fence = fence
redis.call('HSET', KEYS[2], ARGV[1], cjson.encode(entry))
local record = {
  identity = ARGV[1], fence = fence, owner = ARGV[4],
  ceiling = now_ms() + tonumber(ARGV[6]),
}
redis.call('SET', KEYS[3], cjson.encode(record), 'PX', tonumber(ARGV[5]))
return {'started', fence}
"""
)

# KEYS: turn. ARGV: identity, fence, owner, lease_ms.
_RENEW = (
    _COMMON
    + """
local turn = lease(redis.call('GET', KEYS[1]))
if not turn or turn.identity ~= ARGV[1] or turn.fence ~= tonumber(ARGV[2])
  or turn.owner ~= ARGV[3] then return 0 end
local remaining = turn.ceiling - now_ms()
if remaining <= 0 then return 0 end
redis.call('PEXPIRE', KEYS[1], math.min(tonumber(ARGV[4]), remaining))
return 1
"""
)

# KEYS: document, events, turn, messages. ARGV: identity, response, expected document,
# messages, fence, owner.
_FINISH = (
    _COMMON
    + """
local turn = lease(redis.call('GET', KEYS[3]))
if not turn or turn.identity ~= ARGV[1] or turn.fence ~= tonumber(ARGV[5])
  or turn.owner ~= ARGV[6] then return 0 end
local raw = redis.call('HGET', KEYS[2], ARGV[1])
if not raw then return 0 end
local r = cjson.decode(raw)
if r.response or r.fence ~= tonumber(ARGV[5]) then return 0 end
if ARGV[3] ~= '' and redis.call('GET', KEYS[1]) ~= ARGV[3] then return -1 end
local messages = cjson.decode(ARGV[4])
-- Validate everything before the first mutation (Lua failures do not roll back).
local count = redis.call('LLEN', KEYS[4])
local encoded = {}
for i, value in ipairs(messages) do
  -- Preserve JSON arrays and payload bytes: Redis 6 cjson turns [] into {}.
  encoded[i] = string.gsub(value, '"sequence":0,', '"sequence":' .. (count + i) .. ',', 1)
end
for i, value in ipairs(encoded) do redis.call('RPUSH', KEYS[4], value) end
r.response = ARGV[2]
redis.call('HSET', KEYS[2], ARGV[1], cjson.encode(r))
redis.call('DEL', KEYS[3])
return 1
"""
)


class TurnOwnershipLost(EventPermissionError):
    """The attempt no longer owns its turn: expired, reconciled or superseded."""


@dataclass(frozen=True)
class TurnLease:
    """The admitted attempt's ownership: never persisted outside Redis, never a permit."""

    tenant_id: str
    conversation_id: str
    identity: str
    fence: int
    owner: str


class ConversationRepositoryV2:
    def __init__(
        self,
        repository: SessionRedisRepository,
        prefix: str = "caos:communication:v2",
        *,
        lease_ms: int = 15_000,
        max_turn_ms: int = 60_000,
        grace_ms: int = 15_000,
    ) -> None:
        if not 0 < lease_ms <= max_turn_ms + grace_ms or grace_ms <= 0:
            raise ValueError("Invalid turn lease bounds")
        self.repository = repository
        self.prefix = prefix
        self.lease_ms = lease_ms
        # Hard ceiling from admission: no renewal can extend a turn past it.
        self.window_ms = max_turn_ms + grace_ms

    def _keys(self, tenant: str, conversation: str) -> list[str]:
        scope = hashlib.sha256(json.dumps([tenant, conversation]).encode()).hexdigest()
        base = self.prefix + ":{" + scope + "}"
        return [
            base + ":document",
            base + ":events",
            base + ":turn",
            base + ":history:v1:messages",
            base + ":turn:fence",
        ]

    async def _eval(self, script: str, keys: list[str], args: list[Any]) -> Any:
        try:
            async with asyncio.timeout(2):
                return await self.repository.eval(script, keys=keys, args=args)
        except Exception as exc:
            raise EventPermissionError("Conversation storage unavailable") from exc

    async def create(self, conversation: ConversationV2) -> ConversationV2:
        """Trusted writer. Membership changes require a future governed adapter."""
        keys = self._keys(conversation.tenant_id, conversation.id)
        created = await self._eval(
            "return redis.call('SET', KEYS[1], ARGV[1], 'NX')",
            keys[:1],
            [conversation.model_dump_json()],
        )
        if not created:
            raise EventPermissionError("Conversation already exists")
        return conversation

    async def get(self, tenant: str, conversation: str) -> ConversationV2:
        raw = await self._eval(
            "return redis.call('GET', KEYS[1])", self._keys(tenant, conversation)[:1], []
        )
        if not raw:
            raise EventPermissionError("Conversation unavailable")
        document = ConversationV2.model_validate_json(raw)
        if (document.tenant_id, document.id) != (tenant, conversation):
            raise EventPermissionError("Conversation scope mismatch")
        return document

    async def begin(self, tenant: str, user: str, event: ClientEventV2) -> tuple[str, Any]:
        """Admit, replay or reconcile one event.

        Returns ``("started", TurnLease)``, ``("replay", response)`` (including a recovered
        ``interrupted`` result), or ``pending``/``conflict``/``busy``/``full``.
        """
        # Per-user event namespace prevents another participant replaying private results.
        identity = json.dumps([user, event.event_id])
        serialized = event.model_dump_json(
            exclude={"response_mode"} if event.response_mode == "context" else set()
        )
        digest = hashlib.sha256(serialized.encode()).hexdigest()
        keys = self._keys(tenant, event.conversation_id)
        owner = uuid4().hex
        result = await self._eval(
            _BEGIN,
            [keys[0], keys[1], keys[2], keys[4]],
            [
                identity,
                digest,
                json.dumps({"digest": digest, "request": event.model_dump()}),
                owner,
                self.lease_ms,
                self.window_ms,
                event.conversation_id,
            ],
        )
        status = result[0].decode() if isinstance(result[0], bytes) else result[0]
        if status == "started":
            return status, TurnLease(tenant, event.conversation_id, identity, int(result[1]), owner)
        return status, json.loads(result[1]) if len(result) > 1 else None

    async def owns(self, lease: TurnLease) -> bool:
        """Read-only: whether ``lease`` is still the live, owned turn (no renewal)."""
        raw = await self._eval(
            "return redis.call('GET', KEYS[1])",
            self._keys(lease.tenant_id, lease.conversation_id)[2:3],
            [],
        )
        try:
            turn = json.loads(raw) if raw else None
        except ValueError:
            return False
        return isinstance(turn, dict) and (
            turn.get("identity"),
            turn.get("fence"),
            turn.get("owner"),
        ) == (lease.identity, lease.fence, lease.owner)

    async def renew(self, lease: TurnLease) -> bool:
        """Extend the owned lease, never past the turn ceiling. ``False``: ownership lost."""
        renewed = await self._eval(
            _RENEW,
            self._keys(lease.tenant_id, lease.conversation_id)[2:3],
            [lease.identity, lease.fence, lease.owner, self.lease_ms],
        )
        return bool(renewed)

    async def finish(
        self,
        tenant: str,
        user: str,
        event: ClientEventV2,
        response: dict[str, Any],
        *,
        lease: TurnLease,
        messages: tuple[ConversationMessageV2, ...] = (),
        expected_document: ConversationV2 | None = None,
    ) -> None:
        """Commit the one terminal result (and transcript) under the live, owned lease."""
        if (lease.tenant_id, lease.conversation_id, lease.identity) != (
            tenant,
            event.conversation_id,
            json.dumps([user, event.event_id]),
        ):
            raise ValueError("Turn lease scope mismatch")
        if expected_document and (expected_document.tenant_id, expected_document.id) != (
            tenant,
            event.conversation_id,
        ):
            raise ValueError("Conversation snapshot scope mismatch")
        if messages and expected_document is None:
            raise ValueError("Transcript append requires an authorized conversation snapshot")
        if len({m.id for m in messages}) != len(messages):
            raise ValueError("Duplicate transcript IDs")
        for message in messages:
            if (
                (message.tenant_id, message.conversation_id, message.event_id)
                != (
                    tenant,
                    event.conversation_id,
                    event.event_id,
                )
                or message.sequence != 0
                or message.turn_user_id != user
            ):
                raise ValueError("Transcript scope mismatch")
        write = asyncio.create_task(
            self._eval(
                _FINISH,
                self._keys(tenant, event.conversation_id)[:4],
                [
                    lease.identity,
                    json.dumps(response),
                    expected_document.model_dump_json() if expected_document else "",
                    json.dumps([m.model_dump_json() for m in messages]),
                    lease.fence,
                    lease.owner,
                ],
            )
        )
        try:
            result = await asyncio.shield(write)
        except asyncio.CancelledError:
            # Drain an already submitted atomic commit: never abandon its ownership fence.
            await write
            raise
        if result == -1:
            raise EventPermissionError("Conversation membership changed during the turn")
        if result != 1:
            raise TurnOwnershipLost("Conversation turn ownership lost")

    async def read_messages(
        self,
        tenant: str,
        conversation: str,
        *,
        after: int = 0,
        limit: int = 100,
        tail: bool = False,
    ) -> tuple[ConversationMessageV2, ...]:
        """Storage only. Callers MUST apply UTU and message visibility before use."""
        if after < 0 or not 1 <= limit <= 100:
            raise ValueError("Invalid history window")
        start, end = (-limit, -1) if tail else (after, after + limit - 1)
        rows = await self._eval(
            "return redis.call('LRANGE', KEYS[1], ARGV[1], ARGV[2])",
            self._keys(tenant, conversation)[3:4],
            [start, end],
        )
        try:
            messages = tuple(ConversationMessageV2.model_validate_json(row) for row in rows)
        except ValueError as exc:
            raise EventPermissionError("Transcript unavailable") from exc
        if any((m.tenant_id, m.conversation_id) != (tenant, conversation) for m in messages):
            raise EventPermissionError("Transcript scope mismatch")
        return messages

    async def socket_lease(self, tenant: str, session: str, owner: str, action: str) -> bool:
        scope = hashlib.sha256(json.dumps([tenant, session]).encode()).hexdigest()
        key = self.prefix + ":socket:" + scope
        script = """
if ARGV[2] == 'claim' then
  return redis.call('SET', KEYS[1], ARGV[1], 'NX', 'EX', 15) and 1 or 0
end
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
if ARGV[2] == 'release' then return redis.call('DEL', KEYS[1]) end
return redis.call('EXPIRE', KEYS[1], 15)
"""
        return bool(await self._eval(script, [key], [owner, action]))
