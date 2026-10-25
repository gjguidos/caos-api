"""
Domain initialization module.
This module is responsible for initializing all domain-specific containers
and extending the base container with domain-specific dependencies.
"""

from src.base.dependencies.di_container import Container as BaseContainer

# Import domain container extensions
from src.domains.agentverse.dependencies.di_container import (
    extend_container as extend_with_agentverse_domain,
)
from src.domains.agentverse.dependencies.genesis_security import GenesisSecurityBindings
from src.domains.browser_session.dependencies.di_container import (
    extend_container as extend_with_browser_session_domain,
)
from src.domains.green_claim.dependencies.di_container import (
    extend_container as extend_with_green_claim_domain,
)


def initialize_domains(
    container: BaseContainer, *, genesis_security: GenesisSecurityBindings | None = None
) -> BaseContainer:
    """
    Initialize all domain-specific containers and extend the base container.

    Args:
        container: The base container to extend

    Returns:
        The extended container with all domain-specific dependencies
    """
    # Extend the container with each domain
    container = extend_with_browser_session_domain(container)
    container = extend_with_agentverse_domain(container, genesis_security=genesis_security)
    container = extend_with_green_claim_domain(container)

    # Add more domains as needed:

    # container = extend_with_another_domain(container)
    # container = extend_with_yet_another_domain(container)

    return container


__all__ = [
    "initialize_domains",
]
