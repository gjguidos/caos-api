from __future__ import annotations

from fastapi import FastAPI

from src.base.routing.domain_discovery_hooks import (
    register_domain_discovery_hook,
)
from src.domains.agentverse.registries.base import Registry


@register_domain_discovery_hook(
    "agentverse.registry_catalog",
)
def log_registry_catalog(
    _app: FastAPI,
) -> None:
    Registry.log_catalog()