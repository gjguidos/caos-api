
from dependency_injector import containers, providers

from src.base.config.config import settings
from src.base.infrastructure.messaging.rabbitmq.rabbitmq_client import (
    RabbitMQClient,
)
from src.base.repositories.rabbitmq_repository import (
    RabbitMQRepository,
)
from src.base.services.rabbitmq_service import (
    RabbitMQService,
)


class MessagingContainer(containers.DeclarativeContainer):
    """
    RabbitMQ messaging infrastructure.

    Provides:
        - Async RabbitMQ client
        - Messaging repository
        - Messaging service

    Connection lifecycle is managed by FastAPI lifespan.
    """

    rabbitmq_client = providers.Singleton(
        RabbitMQClient,
        url=settings.messaging.url,
    )

    rabbitmq_repository = providers.Singleton(
        RabbitMQRepository,
        rabbitmq_client=rabbitmq_client,
    )

    rabbitmq_service = providers.Singleton(
        RabbitMQService,
        repository=rabbitmq_repository,
    )
