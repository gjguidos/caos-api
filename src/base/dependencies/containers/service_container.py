from dependency_injector import containers, providers

from src.base.services.consumer_info import ConsumerInfoService
from src.base.services.jwt_service import JWTService


class ServiceContainer(containers.DeclarativeContainer):
    """
    Container for service-related dependencies.
    """

    # JWT Service
    jwt_service = providers.Singleton(JWTService)

    # Consumer Information Service
    consumer_info_service = providers.Singleton(ConsumerInfoService)
