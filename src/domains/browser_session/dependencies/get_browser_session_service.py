from __future__ import annotations

from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import Depends

from src.domains.browser_session.dependencies.di_container import BrowserSessionContainer
from src.domains.browser_session.services.browser_session_service import (
    BrowserSessionService,
)


@inject
def get_browser_session_service(
    browser_session_service: BrowserSessionService = Depends(
        Provide[
            BrowserSessionContainer.browser_session_service
        ]
    ),
) -> BrowserSessionService:
    """
    Resolve the browser-session service from the domain DI container.
    """

    return browser_session_service


BrowserSessionServiceDep = Annotated[
    BrowserSessionService,
    Depends(get_browser_session_service),
]