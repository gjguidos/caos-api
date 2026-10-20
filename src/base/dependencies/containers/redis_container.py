from dependency_injector import containers, providers

from src.base.dependencies.containers.redis_resource import redis_resource
from src.base.queues.redis_queue import RedisJobQueue
from src.base.repositories.redis_impl import RedisRepositoryImpl
from src.base.services.queue_service import QueueService
from src.base.services.ws_redis_bridge_service import SocketRedisBridgeService


class RedisContainer(containers.DeclarativeContainer):
    """
    Container for Redis-related dependencies.
    """

    # Redis client
    redis_client = providers.Resource(redis_resource)

    redis_repository = providers.Singleton(RedisRepositoryImpl, redis_client=redis_client)

    job_queue = providers.Singleton(
        RedisJobQueue,
        redis=redis_repository,
    )

    queue_service = providers.Singleton(
        QueueService,
        redis_repository=redis_repository,
        enqueue_concurrency=32,
    )

    socket_redis_bridge_service = providers.Factory(
        SocketRedisBridgeService,
        redis_repository=redis_repository,
    )
