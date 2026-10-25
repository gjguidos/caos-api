"""Redis-backed Genesis conversation documents and bounded durable turn journal.

Uses the same configured repository as durable Genesis sessions. No TTL or
journal eviction: a crashed in-progress turn stays fenced for trusted recovery.
In-flight calls are never automatically retried, including model inference.
"""

import asyncio
import hashlib
import json
from typing import Any

from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.entities.conversation_v2 import ClientEventV2, ConversationV2
from src.domains.agentverse.entities.history_v2 import ConversationMessageV2
from src.domains.agentverse.security.sessions import SessionRedisRepository

_BEGIN = """
local previous = redis.call('HGET', KEYS[2], ARGV[1])
if previous then
  local r = cjson.decode(previous)
  if r.digest ~= ARGV[2] then return {'conflict'} end
  if r.response then return {'replay', r.response} end
  return {'pending'}
end
if redis.call('EXISTS', KEYS[3]) == 1 then return {'busy'} end
if redis.call('HLEN', KEYS[2]) >= 256 then return {'full'} end
redis.call('SET', KEYS[3], ARGV[1])
redis.call('HSET', KEYS[2], ARGV[1], ARGV[3])
return {'started'}
"""
_FINISH = """
if redis.call('GET', KEYS[3]) ~= ARGV[1] then return 0 end
if ARGV[3] ~= '' and redis.call('GET', KEYS[1]) ~= ARGV[3] then return -1 end
local messages = cjson.decode(ARGV[4])
-- Validate everything before the first mutation (Lua failures do not roll back).
local count = redis.call('LLEN', KEYS[4])
local encoded = {}
for i, value in ipairs(messages) do
  -- Preserve JSON arrays and payload bytes: Redis 6 cjson turns [] into {}.
  encoded[i] = string.gsub(value, '"sequence":0,', '"sequence":' .. (count + i) .. ',', 1)
end
local r = cjson.decode(redis.call('HGET', KEYS[2], ARGV[1]))
for i, value in ipairs(encoded) do redis.call('RPUSH', KEYS[4], value) end
r.response = ARGV[2]
redis.call('HSET', KEYS[2], ARGV[1], cjson.encode(r))
redis.call('DEL', KEYS[3])
return 1
"""


class ConversationRepositoryV2:
    def __init__(
        self, repository: SessionRedisRepository, prefix: str = "caos:communication:v2"
    ) -> None:
        self.repository = repository
        self.prefix = prefix

    def _keys(self, tenant: str, conversation: str) -> list[str]:
        scope = hashlib.sha256(json.dumps([tenant, conversation]).encode()).hexdigest()
        base = self.prefix + ":{" + scope + "}"
        return [base + ":document", base + ":events", base + ":turn", base + ":history:v1:messages"]

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
        # Per-user event namespace prevents another participant replaying private results.
        identity = json.dumps([user, event.event_id])
        serialized = event.model_dump_json(
            exclude={"response_mode"} if event.response_mode == "context" else set()
        )
        digest = hashlib.sha256(serialized.encode()).hexdigest()
        result = await self._eval(
            _BEGIN,
            self._keys(tenant, event.conversation_id)[:3],
            [identity, digest, json.dumps({"digest": digest, "request": event.model_dump()})],
        )
        status = result[0].decode() if isinstance(result[0], bytes) else result[0]
        return status, json.loads(result[1]) if len(result) > 1 else None

    async def finish(
        self,
        tenant: str,
        user: str,
        event: ClientEventV2,
        response: dict[str, Any],
        *,
        messages: tuple[ConversationMessageV2, ...] = (),
        expected_document: ConversationV2 | None = None,
    ) -> None:
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
                self._keys(tenant, event.conversation_id),
                [
                    json.dumps([user, event.event_id]),
                    json.dumps(response),
                    expected_document.model_dump_json() if expected_document else "",
                    json.dumps([m.model_dump_json() for m in messages]),
                ],
            )
        )
        try:
            result = await asyncio.shield(write)
        except asyncio.CancelledError:
            # Drain an already submitted atomic commit: never abandon its ownership fence.
            await write
            raise
        if result != 1:
            raise EventPermissionError("Conversation turn ownership lost")

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
            self._keys(tenant, conversation)[3:],
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
