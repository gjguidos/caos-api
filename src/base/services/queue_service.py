from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from redis.exceptions import ResponseError

from src.base.queues.models import AgentVerseJob
from src.base.repositories.redis_repository import RedisRepository

logger = logging.getLogger(__name__)


class QueueService:
    """
    Generic AgentVerse job queue service backed by Redis Streams.
    """

    def __init__(
        self,
        *,
        redis_repository: RedisRepository,
        enqueue_concurrency: int = 32,
    ) -> None:
        if enqueue_concurrency <= 0:
            raise ValueError(
                "enqueue_concurrency must be greater than zero"
            )

        self.redis = redis_repository
        self._enqueue_gate = asyncio.Semaphore(
            enqueue_concurrency
        )

    async def ensure_queue(
        self,
        *,
        queue_name: str,
        consumer_group: str,
    ) -> None:
        logger.debug(
            "Creating Redis consumer group "
            "queue=%s group=%s",
            queue_name,
            consumer_group,
        )

        try:
            await asyncio.wait_for(
                self.redis.xgroup_create(
                    name=queue_name,
                    groupname=consumer_group,
                    id="0",
                    mkstream=True,
                ),
                timeout=10,
            )

        except TimeoutError as exc:
            logger.exception(
                "Timed out creating Redis consumer group "
                "queue=%s group=%s",
                queue_name,
                consumer_group,
            )
            raise RuntimeError(
                "Redis XGROUP CREATE timed out"
            ) from exc

        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                logger.exception(
                    "Failed creating Redis consumer group "
                    "queue=%s group=%s",
                    queue_name,
                    consumer_group,
                )
                raise

            logger.debug(
                "Redis consumer group already exists "
                "queue=%s group=%s",
                queue_name,
                consumer_group,
            )

        else:
            logger.debug(
                "Redis consumer group ready "
                "queue=%s group=%s",
                queue_name,
                consumer_group,
            )

    async def enqueue(
        self,
        *,
        queue_name: str,
        job: AgentVerseJob,
    ) -> str:
        payload = await asyncio.to_thread(
            _serialize_job,
            job,
        )

        async with self._enqueue_gate:
            message_id = await self.redis.xadd(
                queue_name,
                payload,
            )

        logger.debug(
            "Enqueued job queue=%s job_id=%s "
            "run_id=%s message_id=%s",
            queue_name,
            job.job_id,
            job.run_id,
            message_id,
        )

        return message_id

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

        messages: list[tuple[str, AgentVerseJob]] = []

        for _, entries in response:
            for message_id, fields in entries:
                messages.append(
                    self._parse_message(
                        message_id,
                        fields,
                    )
                )

        if messages:
            logger.debug(
                "Consumed messages queue=%s group=%s "
                "consumer=%s count=%d",
                queue_name,
                consumer_group,
                consumer_name,
                len(messages),
            )

        return messages

    async def acknowledge(
        self,
        *,
        queue_name: str,
        consumer_group: str,
        message_id: str,
    ) -> None:
        await self.redis.xack(
            queue_name,
            consumer_group,
            message_id,
        )

        await self.redis.xdel(
            queue_name,
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

        entries = result[1]

        messages = [
            self._parse_message(
                message_id,
                fields,
            )
            for message_id, fields in entries
        ]

        if messages:
            logger.debug(
                "Reclaimed stale messages queue=%s "
                "group=%s consumer=%s count=%d",
                queue_name,
                consumer_group,
                consumer_name,
                len(messages),
            )

        return messages
    
    async def delete_queue(
        self,
        *,
        queue_name: str,
    ) -> None:
        await self.redis.delete(queue_name)

        logger.debug(
            "Deleted Redis job queue queue=%s",
            queue_name,
        )

    @classmethod
    def _parse_message(
        cls,
        message_id: Any,
        fields: dict[Any, Any],
    ) -> tuple[str, AgentVerseJob]:
        normalized_fields = {
            cls._decode(key): cls._decode(value)
            for key, value in fields.items()
        }

        job = AgentVerseJob.model_validate(
            json.loads(
                normalized_fields["job"]
            )
        )

        return (
            cls._decode(message_id),
            job,
        )

    @staticmethod
    def _decode(value: Any) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8")

        return str(value)
    
def _serialize_job(job: AgentVerseJob) -> dict[str, str]:
    return {
        "schema": job.schema_name,
        "job": json.dumps(
            job.model_dump(
                mode="json",
                by_alias=True,
            ),
            ensure_ascii=False,
        ),
}