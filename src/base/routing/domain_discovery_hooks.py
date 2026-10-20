from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI


DomainDiscoveryHook = Callable[[FastAPI], None]

_hooks: dict[str, DomainDiscoveryHook] = {}


def register_domain_discovery_hook(
    key: str,
) -> Callable[[DomainDiscoveryHook], DomainDiscoveryHook]:
    """
    Register a callback that runs after domain/router discovery completes.

    Registration is last-write-wins because domain modules may be
    re-executed by the auto-router.
    """

    def decorator(
        hook: DomainDiscoveryHook,
    ) -> DomainDiscoveryHook:
        _hooks[key] = hook
        return hook

    return decorator


def run_domain_discovery_hooks(
    app: FastAPI,
) -> None:
    """
    Run all callbacks registered for the completed domain-discovery phase.
    """

    for hook in tuple(_hooks.values()):
        hook(app)