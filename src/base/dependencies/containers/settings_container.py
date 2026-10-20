from dependency_injector import containers, providers

from src.base.config.config import Settings
from src.base.dependencies.containers.helpers.build_public_agent_settings import (
    build_public_agent_settings,
)


class SettingsContainer(containers.DeclarativeContainer):
    """
    Container for settings-related dependencies.
    """

    settings = providers.Singleton(Settings)

    public_agent_settings = providers.Singleton(
        build_public_agent_settings,
        settings=settings,
    )