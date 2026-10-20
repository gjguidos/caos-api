# src/base/repositories/redis_repository.py
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any


class RedisRepository(ABC):
    """Abstract interface for Redis operations."""

    # ---- KV ----
    @abstractmethod
    async def get(self, key: str) -> str | None:
        pass

    @abstractmethod
    async def get_raw(
        self,
        key: str,
    ) -> bytes | str | None:
        pass

    @abstractmethod
    async def set(
        self, key: str, value: str, expiration: int | None = None, nx: bool = False
    ) -> bool:
        """Set key. If nx=True, only set if key does not exist."""
        pass

    @abstractmethod
    async def delete(self, key: str) -> bool:
        pass

    @abstractmethod
    async def expire(self, key: str, seconds: int) -> bool:
        pass

    @abstractmethod
    async def exists(self, key: str) -> bool:
        pass

    @abstractmethod
    async def scan_keys(self, pattern: str) -> list[str]:
        pass

    # ---- Lists (history) ----
    @abstractmethod
    async def lpush(self, key: str, value: str) -> int:
        pass

    @abstractmethod
    async def ltrim(self, key: str, start: int, end: int) -> bool:
        pass

    @abstractmethod
    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        pass

    # ---- Sets (presence) ----
    @abstractmethod
    async def sadd(self, key: str, *members: str) -> int:
        pass

    @abstractmethod
    async def srem(self, key: str, *members: str) -> int:
        pass

    @abstractmethod
    async def scard(self, key: str) -> int:
        pass

    @abstractmethod
    async def smembers(self, key: str) -> set[str]:
        pass

    # ---- Scripting (locks) ----
    @abstractmethod
    async def eval(self, script: str, keys: list[str], args: list[Any]) -> Any:
        pass

    # ---- Pub/Sub (UI bridge) ----
    @abstractmethod
    async def subscribe_channel(self, channel: str) -> AsyncIterator[str]:
        """Yield messages (as strings) published to `channel`."""
        pass

    @abstractmethod
    async def publish(self, channel: str, payload: Any) -> int:
        """Publish payload to channel. Return subscriber count if available."""
        pass

    # ---- QoL ----
    @abstractmethod
    async def ping(self) -> bool:
        pass

    @abstractmethod
    async def close(self) -> None:
        pass

    @abstractmethod
    async def xgroup_create(
        self,
        name: str,
        groupname: str,
        id: str = "0",
        mkstream: bool = False,
    ) -> Any:
        pass

    @abstractmethod
    async def xadd(
        self,
        name: str,
        fields: dict[str, Any],
    ) -> str:
        pass

    @abstractmethod
    async def xreadgroup(
        self,
        groupname: str,
        consumername: str,
        streams: dict[str, str],
        count: int | None = None,
        block: int | None = None,
    ) -> list[Any]:
        pass

    @abstractmethod
    async def xack(
        self,
        name: str,
        groupname: str,
        *message_ids: str,
    ) -> int:
        pass

    @abstractmethod
    async def xautoclaim(
        self,
        name: str,
        groupname: str,
        consumername: str,
        min_idle_time: int,
        start_id: str = "0-0",
        count: int | None = None,
    ) -> Any:
        pass
    
    @abstractmethod
    async def xdel(
        self,
        name: str,
        *message_ids: str,
    ) -> int:
        pass

    @abstractmethod
    async def xack_and_delete(
        self,
        name: str,
        groupname: str,
        message_id: str,
    ) -> None:
        pass