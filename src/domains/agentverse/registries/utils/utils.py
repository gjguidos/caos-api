import logging

from src.domains.agentverse.registries.base import Registry
from src.domains.agentverse.registries.registries import registries

logger = logging.getLogger(__name__)


def get_registry(name: str) -> Registry:
    if name not in registries:
        raise KeyError(f"Registry '{name}' not found")
    logger.debug(f"Retrieved registry: {name}")
    return registries[name]


__all__ = ["get_registry"]
