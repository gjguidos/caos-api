"""Read-only durability verification: python -m scripts.verify_genesis_redis.

Run in the API container after approved Redis maintenance. Does not write keys,
set configuration, provision policy, restart Redis, or print credentials.
"""

import asyncio
from typing import Any

from src.base.dependencies.containers.redis_resource import redis_resource


def text(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


async def main() -> None:
    async with redis_resource() as client:
        for name, expected in {
            "appendonly": "yes",
            "appendfsync": "always",
            "maxmemory-policy": "noeviction",
            "protected-mode": "yes",
        }.items():
            values = {text(k): text(v) for k, v in (await client.config_get(name)).items()}
            if values.get(name) != expected:
                raise RuntimeError(f"Redis {name} must be {expected}")
            print(f"{name}={expected}")
        persistence = await client.info("persistence")
        if persistence.get("aof_enabled") != 1 or persistence.get("aof_last_write_status") != "ok":
            raise RuntimeError("Redis AOF is disabled or has a write failure")
        if persistence.get("loading") or persistence.get("aof_rewrite_in_progress"):
            raise RuntimeError("Wait for Redis loading/AOF rewrite to complete")
        print("AOF enabled; last write status ok. Verify port/volume/NOAUTH separately.")


if __name__ == "__main__":
    asyncio.run(main())
