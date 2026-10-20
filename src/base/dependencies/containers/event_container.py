from dependency_injector import containers, providers

from src.base.events.redis_event_publisher import RedisDomainEventPublisher


class EventContainer(containers.DeclarativeContainer):
    redis_repository = providers.Dependency()

    domain_event_publisher = providers.Singleton(
        RedisDomainEventPublisher,
        redis_repository=redis_repository,
        channel="agentverse:domain-events",
    )