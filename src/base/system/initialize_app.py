import logging
import os
from collections.abc import Callable

from fastapi import FastAPI

from src.base.config.config import settings
from src.base.config.openapi_config import setup_openapi
from src.base.dependencies.di_container import Container
from src.base.handlers.exception import register_exception_handlers
from src.base.middlewares.register_middleware import register_middleware
from src.base.routing.auto_routing import register_all_routers
from src.base.routing.domain_discovery_hooks import (
    run_domain_discovery_hooks,
)
from src.base.system.routes import setup_internal_routes
from src.base.utils.env_utils import is_local_development
from src.domains import initialize_domains

logger = logging.getLogger(settings.texts_new.initializers.app["logger_name"])


def initialize_app(
    app: FastAPI, *, domain_security: dict | None = None,
    domain_security_factory: Callable[[Container], dict] | None = None,
) -> FastAPI:
    """
    Initialize the FastAPI app with all necessary configurations, routes, and middleware.

    Args:
        app (FastAPI): The FastAPI application instance.
        domain_security: Optional trusted domain bootstrap bindings. Never
            populate from request input. Missing Genesis bindings deny activation.

    Returns:
        FastAPI: The configured FastAPI application instance.
    """
    # Create and configure the dependency injection container
    logger.info(settings.texts_new.initializers.app["initialize_server"])
    container = Container()

    # Initialize resources (if needed)
    if hasattr(container, "init_resources"):
        container.init_resources()

    # Initialize domain-specific containers
    logger.info(settings.texts_new.initializers.app["initialize_business_domains"])
    if domain_security_factory is not None:
        if domain_security is not None:
            raise ValueError("Choose explicit security bindings or a security factory")
        domain_security = domain_security_factory(container)
    container = initialize_domains(container, **(domain_security or {}))

    # Attach the container to the app
    logger.info(settings.texts_new.initializers.app["attaching_di"])
    app.container = container
    if domain_security and domain_security.get("genesis_security") is not None:
        from src.domains.agentverse.dependencies.genesis_bootstrap import (
            configure_genesis_transport,
        )

        configure_genesis_transport(app, domain_security["genesis_security"])

    # Register domain routes
    logger.info(settings.texts_new.initializers.app["registering_domain_routes"])
    # register_domain_routes(app)
    DOMAINS_PATH = os.path.abspath("src/domains")
    BASE_MODULE = "src.domains"  # Base Python module
    
    register_all_routers(app, DOMAINS_PATH, BASE_MODULE)
    
    logger.info("Domain route discovery completed")

    # Run domain discovery hooks
    run_domain_discovery_hooks(app)

    # Integrate Prometheus metrics with basic configuration
    logger.info(settings.texts_new.initializers.app["integrating_prometheus_metrics"])

    # Setup internal routes
    logger.info(settings.texts_new.initializers.app["registering_event_handlers"])
    setup_internal_routes(app)

    # Register exception handlers
    logger.info(settings.texts_new.initializers.app["registering_internal_routes"])
    register_exception_handlers(app)

    # Register middleware
    logger.info(settings.texts_new.initializers.app["registering_middleware"])
    register_middleware(app)

    logger.info(settings.texts_new.initializers.app["server_initialized"])

    # Setup OpenAPI configuration (no-op outside local development)
    if is_local_development():
        setup_openapi(app)

    return app
