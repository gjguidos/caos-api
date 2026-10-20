from dependency_injector import containers, providers

from src.base.websockets.event_router import EventRouter


class SocketContainer(containers.DeclarativeContainer):
    """
    Container for socket related dependencies.
    """

    event_router = providers.Singleton(EventRouter)
