from dependency_injector.wiring import Provide, inject
from fastapi import Depends

from src.base.config.config import Settings
from src.base.dependencies.di_container import Container
from src.base.handlers.log_event_handlers import (
    handle_error_event,
    handle_rate_limit_event,
    handle_security_event,
)


@inject
def get_settings(
    settings: Settings = Depends(Provide[Container.settings_container.settings]),
) -> Settings:
    """
    Dependency that provides the Settings instance with handlers injected.
    """
    # Dynamically inject handlers into the settings instance
    settings.set_handlers(
        error_handler=handle_error_event,
        security_handler=handle_security_event,
        rate_limit_handler=handle_rate_limit_event,
    )
