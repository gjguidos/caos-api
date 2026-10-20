from __future__ import annotations

import logging
from types import SimpleNamespace

from dependency_injector import containers, providers

from src.base.dependencies.di_container import Container as BaseContainer
from src.domains.browser_session.clients.browser_runner_client import (
    BrowserRunnerClient,
)
from src.domains.browser_session.services.browser_session_bootstrapper import (
    BrowserSessionBootstrapper,
)
from src.domains.browser_session.services.browser_session_service import (
    BrowserSessionService,
)
from src.domains.browser_session.store.browser_session_store import (
    BrowserSessionStore,
)

logger = logging.getLogger(__name__)

class BrowserSessionContainer(containers.DeclarativeContainer):
    app_settings = providers.Dependency()
    http_repository = providers.Dependency()

    cache_repository = providers.Dependency()
    event_publisher = providers.Dependency()

    browser_session_settings = providers.Callable(
        lambda s: s.browser_session,
        app_settings,
    )

    cache_settings = providers.Callable(
        lambda s: s.cache,
        app_settings,
    )

    redis_prefix = providers.Callable(
        lambda c: c.redis_prefix,
        cache_settings,
    )

    redis_key_ns = providers.Callable(
        lambda c: c.redis_key_ns,
        cache_settings,
    )

    key_version = providers.Callable(
        lambda c: int(
            getattr(
                c,
                "redis_browser_session_key_version",
                1,
            )
        ),
        cache_settings,
    )

    default_ttl_seconds = providers.Callable(
        lambda c: int(
            getattr(
                c,
                "redis_browser_session_ttl",
                1800,
            )
        ),
        cache_settings,
    )

    multi_tenant = providers.Callable(
        lambda c: bool(
            getattr(
                c,
                "redis_browser_session_key_multi_tenant",
                getattr(
                    c,
                    "redis_session_key_multi_tenant",
                    False,
                ),
            )
        ),
        cache_settings,
    )

    browser_session_store = providers.Factory(
        BrowserSessionStore,
        cache_repository=cache_repository,
        redis_prefix=redis_prefix,
        redis_key_ns=redis_key_ns,
        key_version=key_version,
        default_ttl_seconds=default_ttl_seconds,
        multi_tenant=multi_tenant,
    )

    browser_runner_client = providers.Singleton(
        BrowserRunnerClient,
        http_repository=http_repository,
        base_url=providers.Callable(
            lambda s: s.browser_runner_base_url,
            browser_session_settings,
        ),
        start_session_path=providers.Callable(
            lambda s: s.browser_runner_start_session_path,
            browser_session_settings,
        ),
        status_path_template=providers.Callable(
            lambda s: s.browser_runner_status_path_template,
            browser_session_settings,
        ),
        storage_state_path_template=providers.Callable(
            lambda s: s.browser_runner_storage_state_path_template,
            browser_session_settings,
        ),
        delete_session_path_template=providers.Callable(
            lambda s: s.browser_runner_delete_session_path_template,
            browser_session_settings,
        ),
        render_page_path_template=providers.Callable(
            lambda s: s.browser_runner_render_page_path_template,
            browser_session_settings,
        ),
        ephemeral_render_path=providers.Callable(
            lambda s: s.browser_runner_ephemeral_render_path,
            browser_session_settings,
        ),
    )

    browser_session_bootstrapper = providers.Factory(
        BrowserSessionBootstrapper,
        browser_runner_client=browser_runner_client,
    )

    browser_session_service = providers.Factory(
        BrowserSessionService,
        store=browser_session_store,
        bootstrapper=browser_session_bootstrapper,
        event_publisher=event_publisher,
        browser_session_callback_url=providers.Callable(
            lambda s: s.callback_url,
            browser_session_settings,
        ),
        browser_session_callback_token=providers.Callable(
            lambda s: s.callback_token,
            browser_session_settings,
        ),
    )


def extend_container(
    base_container: BaseContainer,
) -> BaseContainer:
    browser_session = BrowserSessionContainer()

    browser_session.app_settings.override(
        base_container.settings_container.settings
    )

    browser_session.http_repository.override(
        base_container.http.default_http_repository
    )

    browser_session.cache_repository.override(
        base_container.redis.redis_repository
    )

    browser_session.event_publisher.override(
        base_container.events.domain_event_publisher
    )

    browser_session.wire(
        modules=[
            "src.domains.browser_session.dependencies.get_browser_session_service",
            "src.domains.browser_session.interfaces.api.v1.routes",
        ]
    )

    if not hasattr(base_container, "browser_session"):
        base_container.browser_session = SimpleNamespace()

    namespace = base_container.browser_session
    namespace.browser_session_store = browser_session.browser_session_store
    namespace.browser_runner_client = browser_session.browser_runner_client
    namespace.browser_session_bootstrapper = browser_session.browser_session_bootstrapper
    namespace.browser_session_service = browser_session.browser_session_service
    namespace.container = browser_session

    return base_container