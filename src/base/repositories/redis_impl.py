from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import redis.asyncio as redis
from redis.exceptions import ResponseError

from src.base.repositories.redis_repository import (
    RedisRepository,
)


class RedisRepositoryImpl(
    RedisRepository
):
    def __init__(
        self,
        redis_client: redis.Redis,
    ):
        self.redis = redis_client
        # Redis 6.0 has no XAUTOCLAIM. Keep a per-stream/group scan cursor for
        # the XPENDING/XCLAIM compatibility path so workers don't repeatedly
        # inspect only the oldest pending messages.
        self._xautoclaim_fallback_cursors: dict[tuple[str, str], str] = {}

    # --------------------------------------------------------------
    # KV
    # --------------------------------------------------------------
    async def get(
        self,
        key: str,
    ) -> str | None:
        val = await self.redis.get(
            key
        )

        if val is None:
            return None

        if isinstance(
            val,
            bytes | bytearray,
        ):
            return val.decode(
                "utf-8"
            )

        return str(
            val
        )

    async def get_raw(
        self,
        key: str,
    ) -> bytes | str | None:
        return await self.redis.get(
            key
        )

    async def set(
        self,
        key: str,
        value: str,
        expiration: int | None = None,
        nx: bool = False,
    ) -> bool:
        kwargs: dict[str, Any] = {}

        if expiration is not None:
            kwargs["ex"] = int(
                expiration
            )

        if nx:
            kwargs["nx"] = True

        return bool(
            await self.redis.set(
                key,
                value,
                **kwargs,
            )
        )

    async def delete(
        self,
        key: str,
    ) -> bool:
        return bool(
            await self.redis.delete(
                key
            )
        )

    async def expire(
        self,
        key: str,
        seconds: int,
    ) -> bool:
        return bool(
            await self.redis.expire(
                key,
                int(seconds),
            )
        )

    async def exists(
        self,
        key: str,
    ) -> bool:
        return bool(
            await self.redis.exists(
                key
            )
        )

    async def scan_keys(
        self,
        pattern: str,
    ) -> list[str]:
        keys: list[str] = []

        async for key in self.redis.scan_iter(
            match=pattern
        ):
            keys.append(
                key.decode()
                if isinstance(
                    key,
                    bytes | bytearray,
                )
                else str(key)
            )

        return keys

    # --------------------------------------------------------------
    # Lists
    # --------------------------------------------------------------
    async def lpush(
        self,
        key: str,
        value: str,
    ) -> int:
        return int(
            await self.redis.lpush(
                key,
                value,
            )
        )

    async def ltrim(
        self,
        key: str,
        start: int,
        end: int,
    ) -> bool:
        await self.redis.ltrim(
            key,
            start,
            end,
        )

        return True

    async def lrange(
        self,
        key: str,
        start: int,
        end: int,
    ) -> list[str]:
        vals = await self.redis.lrange(
            key,
            start,
            end,
        )

        return [
            value.decode()
            if isinstance(
                value,
                bytes | bytearray,
            )
            else str(value)
            for value in vals
        ]

    # --------------------------------------------------------------
    # Sets
    # --------------------------------------------------------------
    async def sadd(
        self,
        key: str,
        *members: str,
    ) -> int:
        return int(
            await self.redis.sadd(
                key,
                *members,
            )
        )

    async def srem(
        self,
        key: str,
        *members: str,
    ) -> int:
        return int(
            await self.redis.srem(
                key,
                *members,
            )
        )

    async def scard(
        self,
        key: str,
    ) -> int:
        return int(
            await self.redis.scard(
                key
            )
        )

    async def smembers(
        self,
        key: str,
    ) -> set[str]:
        items = await self.redis.smembers(
            key
        )

        return {
            item.decode()
            if isinstance(
                item,
                bytes | bytearray,
            )
            else str(item)
            for item in items
        }

    # --------------------------------------------------------------
    # Scripting
    # --------------------------------------------------------------
    async def eval(
        self,
        script: str,
        keys: list[str],
        args: list[Any],
    ) -> Any:
        return await self.redis.eval(
            script,
            len(keys),
            *keys,
            *args,
        )

    # --------------------------------------------------------------
    # Pub/Sub
    # --------------------------------------------------------------
    async def subscribe_channel(
        self,
        channel: str,
    ) -> AsyncIterator[str]:
        pubsub = self.redis.pubsub()

        await pubsub.subscribe(
            channel
        )

        try:
            async for msg in pubsub.listen():
                if msg.get("type") != "message":
                    continue

                data = msg.get(
                    "data"
                )

                if isinstance(
                    data,
                    bytes | bytearray,
                ):
                    yield data.decode()
                else:
                    yield str(
                        data
                    )
        finally:
            try:
                await pubsub.unsubscribe(
                    channel
                )
            finally:
                await pubsub.close()

    async def publish(
        self,
        channel: str,
        payload: Any,
    ) -> int:
        if not isinstance(
            payload,
            str | bytes | bytearray,
        ):
            payload = json.dumps(
                payload,
                ensure_ascii=False,
            )

        return int(
            await self.redis.publish(
                channel,
                payload,
            )
        )

    # --------------------------------------------------------------
    # QoL
    # --------------------------------------------------------------
    async def ping(
        self,
    ) -> bool:
        return bool(
            await self.redis.ping()
        )

    async def close(
        self,
    ) -> None:
        close = (
            getattr(
                self.redis,
                "aclose",
                None,
            )
            or self.redis.close
        )

        result = close()

        if hasattr(
            result,
            "__await__",
        ):
            await result

    async def xgroup_create(
        self,
        name: str,
        groupname: str,
        id: str = "0",
        mkstream: bool = False,
    ) -> Any:
        return await self.redis.xgroup_create(
            name=name,
            groupname=groupname,
            id=id,
            mkstream=mkstream,
        )

    async def xadd(
        self,
        name: str,
        fields: dict[str, Any],
    ) -> str:
        message_id = await self.redis.xadd(
            name,
            fields,
        )

        return (
            message_id.decode()
            if isinstance(message_id, bytes)
            else str(message_id)
        )

    async def xdel(
        self,
        name: str,
        *message_ids: str,
    ) -> int:
        return int(
            await self.redis.xdel(
                name,
                *message_ids,
            )
        )

    async def xreadgroup(
        self,
        groupname: str,
        consumername: str,
        streams: dict[str, str],
        count: int | None = None,
        block: int | None = None,
    ) -> list[Any]:
        return await self.redis.xreadgroup(
            groupname=groupname,
            consumername=consumername,
            streams=streams,
            count=count,
            block=block,
        )

    async def xack(
        self,
        name: str,
        groupname: str,
        *message_ids: str,
    ) -> int:
        return int(
            await self.redis.xack(
                name,
                groupname,
                *message_ids,
            )
        )

    async def xautoclaim(
        self,
        name: str,
        groupname: str,
        consumername: str,
        min_idle_time: int,
        start_id: str = "0-0",
        count: int | None = None,
    ) -> Any:
        try:
            return await self.redis.xautoclaim(
                name=name,
                groupname=groupname,
                consumername=consumername,
                min_idle_time=min_idle_time,
                start_id=start_id,
                count=count,
            )
        except ResponseError as exc:
            error = str(exc).lower()
            if "unknown command" not in error or "xautoclaim" not in error:
                raise

            return await self._xautoclaim_redis_6_fallback(
                name=name,
                groupname=groupname,
                consumername=consumername,
                min_idle_time=min_idle_time,
                start_id=start_id,
                count=count,
            )

    async def _xautoclaim_redis_6_fallback(
        self,
        *,
        name: str,
        groupname: str,
        consumername: str,
        min_idle_time: int,
        start_id: str,
        count: int | None,
    ) -> tuple[str, list[Any], list[str]]:
        """Approximate XAUTOCLAIM using Redis 5/6-compatible commands.

        Redis 6.0 supports XPENDING and XCLAIM, but not XPENDING's IDLE filter
        or exclusive ranges. Pages therefore use an inclusive lower bound and
        discard the previous page's final ID. The local cursor advances between
        worker polls; XCLAIM checks the idle threshold atomically before moving
        ownership, so the XPENDING idle-time check is only an optimization.
        """
        cursor_key = (name, groupname)
        cursor = start_id
        if cursor == "0-0":
            cursor = self._xautoclaim_fallback_cursors.get(
                cursor_key,
                "0-0",
            )

        page_size = max(1, count or 100)
        # A modest scan window gives XAUTOCLAIM-like progress without loading
        # an unbounded pending list on every worker poll.
        scan_size = max(100, page_size)
        requested_count = scan_size + (1 if cursor != "0-0" else 0)
        pending = await self.redis.xpending_range(
            name,
            groupname,
            min=cursor,
            max="+",
            count=requested_count,
        )
        has_more = len(pending) == requested_count

        if cursor != "0-0" and pending:
            first_id = self._as_text(pending[0].get("message_id"))
            if first_id == cursor:
                pending = pending[1:]

        candidate_ids: list[str] = []
        for entry in pending:
            message_id = self._as_text(entry.get("message_id"))
            idle_ms = int(entry.get("time_since_delivered", 0))
            if idle_ms >= min_idle_time:
                candidate_ids.append(message_id)
                if len(candidate_ids) >= page_size:
                    break

        # Advance across the page we examined, not merely the messages claimed.
        # This avoids a recent pending entry at the head starving older work.
        examined = pending[:scan_size]
        if has_more and examined:
            next_cursor = self._as_text(examined[-1].get("message_id"))
        else:
            next_cursor = "0-0"
        self._xautoclaim_fallback_cursors[cursor_key] = next_cursor

        claimed = (
            await self.redis.xclaim(
                name,
                groupname,
                consumername,
                min_idle_time,
                candidate_ids,
            )
            if candidate_ids
            else []
        )
        # Match redis-py's XAUTOCLAIM response shape: cursor, entries, deleted IDs.
        return next_cursor, claimed, []

    @staticmethod
    def _as_text(value: Any) -> str:
        if isinstance(value, bytes | bytearray):
            return value.decode("utf-8")
        return str(value)

    async def xack_and_delete(
        self,
        name: str,
        groupname: str,
        message_id: str,
    ) -> None:
        pipe = self.redis.pipeline(
            transaction=True,
        )

        pipe.xack(
            name,
            groupname,
            message_id,
        )
        pipe.xdel(
            name,
            message_id,
        )

        await pipe.execute()
