from __future__ import annotations

import json
from typing import Any

from redis.exceptions import ResponseError

from src.base.queues.models import AgentVerseJob
from src.base.repositories.redis_repository import RedisRepository


class RedisJobQueue:
    def __init__(
        self,
        *,
        redis: RedisRepository,
    ) -> None:
        self.redis = redis

    async def ensure_group(
        self,
        *,
        queue_name: str,
        consumer_group: str,
    ) -> None:
        try:
            await self.redis.xgroup_create(
                name=queue_name,
                groupname=consumer_group,
                id="0",
                mkstream=True,
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    async def publish(
        self,
        *,
        queue_name: str,
        job: AgentVerseJob,
    ) -> str:
        return await self.redis.xadd(
            queue_name,
            {
                "schema": job.schema,
                "job": json.dumps(
                    job.model_dump(mode="json"),
                    ensure_ascii=False,
                ),
            },
        )

    async def consume(
        self,
        *,
        queue_name: str,
        consumer_group: str,
        consumer_name: str,
        count: int = 1,
        block_ms: int = 5_000,
    ) -> list[tuple[str, AgentVerseJob]]:
        response = await self.redis.xreadgroup(
            groupname=consumer_group,
            consumername=consumer_name,
            streams={
                queue_name: ">",
            },
            count=count,
            block=block_ms,
        )

        messages: list[
            tuple[str, AgentVerseJob]
        ] = []

        for _, entries in response:
            for message_id, fields in entries:
                messages.append(
                    self._decode_message(
                        message_id,
                        fields,
                    )
                )

        return messages

    async def acknowledge(
        self,
        *,
        queue_name: str,
        consumer_group: str,
        message_id: str,
    ) -> None:
        await self.redis.xack_and_delete(
            queue_name,
            consumer_group,
            message_id,
        )

    async def reclaim_stale(
        self,
        *,
        queue_name: str,
        consumer_group: str,
        consumer_name: str,
        min_idle_ms: int,
        count: int = 100,
    ) -> list[tuple[str, AgentVerseJob]]:
        result = await self.redis.xautoclaim(
            name=queue_name,
            groupname=consumer_group,
            consumername=consumer_name,
            min_idle_time=min_idle_ms,
            start_id="0-0",
            count=count,
        )

        # Redis returns:
        # (next_start_id, [(message_id, fields)], deleted_ids)
        entries = result[1]

        return [
            self._decode_message(
                message_id,
                fields,
            )
            for message_id, fields in entries
        ]

    @classmethod
    def _decode_message(
        cls,
        message_id: Any,
        fields: dict[Any, Any],
    ) -> tuple[str, AgentVerseJob]:
        normalized = {
            cls._decode(key): cls._decode(value)
            for key, value in fields.items()
        }

        return (
            cls._decode(message_id),
            AgentVerseJob.model_validate(
                json.loads(normalized["job"])
            ),
        )

    @staticmethod
    def _decode(value: Any) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8")

        return str(value)