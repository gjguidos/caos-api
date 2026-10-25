# src/base/containers/redis_resource.py

import logging
from contextlib import asynccontextmanager

import redis.asyncio as redis
from redis_entraid.cred_provider import (
    create_from_default_azure_credential,
)

from src.base.config.config import settings
from src.base.utils.env_utils import is_local_development

logger = logging.getLogger("redis")


@asynccontextmanager
async def redis_resource():
    c = settings.cache

    # --------------------------------------------------------------
    # Redis client
    #
    # Keep the underlying Redis client binary.
    #
    # RedisRepositoryImpl is responsible for converting responses into
    # strings where its public API requires strings.
    #
    # This also allows consumers such as SiteStateStore to use
    # get_raw() and avoid decoding large payloads on the event loop.
    # --------------------------------------------------------------
    if is_local_development():
        logger.info(
            "Redis mode: Local Redis env=%s host=%s",
            getattr(
                settings.application,
                "environment",
                "development",
            ),
            c.redis_host,
        )

        client = redis.from_url(
            c.redis_url,
            encoding="utf-8",
            decode_responses=False,
            health_check_interval=30,
            retry_on_timeout=True,
            socket_timeout=(
                c.redis_read_timeout_ms
                / 1000.0
            ),
            socket_connect_timeout=(
                c.redis_connect_timeout_ms
                / 1000.0
            ),
            max_connections=c.redis_pool_max,
        )

        pool = redis.BlockingConnectionPool.from_url(
            c.redis_url,
            encoding="utf-8",
            decode_responses=False,
            health_check_interval=30,
            retry_on_timeout=True,
            socket_timeout=(
                c.redis_read_timeout_ms / 1000.0
            ),
            socket_connect_timeout=(
                c.redis_connect_timeout_ms / 1000.0
            ),
            max_connections=c.redis_pool_max,
            timeout=30,
        )

        client = redis.Redis(
            connection_pool=pool,
        )

    else:
        cred_provider = (
            create_from_default_azure_credential(
                (
                    "https://redis.azure.com/.default",
                )
            )
        )
        
        pool = redis.BlockingConnectionPool.from_url(
            c.redis_url,
            encoding="utf-8",
            decode_responses=False,
            credential_provider=cred_provider,
            health_check_interval=30,
            retry_on_timeout=True,
            socket_timeout=c.redis_read_timeout_ms / 1000.0,
            socket_connect_timeout=c.redis_connect_timeout_ms / 1000.0,
            max_connections=c.redis_pool_max,
            timeout=30,
        )

        client = redis.Redis(
            connection_pool=pool,
        )


        logger.info(
            (
                "Redis mode: Azure for Redis using "
                "System Assigned Identity env=%s host=%s pool_max=%d"
            ),
            getattr(
                settings.application,
                "environment",
                "development",
            ),
            c.redis_host,
            c.redis_pool_max,
        )

    # Fail fast.
    await client.ping()

    try:
        yield client

    finally:
        close = (
            getattr(
                client,
                "aclose",
                None,
            )
            or client.close
        )

        result = close()

        if hasattr(
            result,
            "__await__",
        ):
            await result
