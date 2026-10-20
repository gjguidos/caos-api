from __future__ import annotations

import logging

from dependency_injector import containers, providers

from src.base.config.config import settings
from src.base.services.authz_client import AuthzClient

logger = logging.getLogger(__name__)


def _create_authz_client() -> AuthzClient | None:
    cfg = settings.user_management

    if not cfg.authz_enabled:
        logger.warning("AUTHZ_ENABLED is set to false - all permission checks will be skipped.")
        return None

    if not cfg.authz_url:
        raise RuntimeError(
            "AUTHZ_ENABLED is true but AUTHZ_URL is not set. "
            "Either set AUTHZ_URL or set AUTHZ_ENABLED=false."
        )

    return AuthzClient(
        base_url=cfg.authz_url,
        timeout=cfg.authz_timeout,
        cache_ttl=cfg.authz_cache_ttl,
    )


class AuthzContainer(containers.DeclarativeContainer):
    """Dependency injection container for the User Management authorisation client."""

    authz_client = providers.Singleton(_create_authz_client)
