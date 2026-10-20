from dependency_injector import containers


class WiringContainer(containers.DeclarativeContainer):
    """
    Container for wiring-related dependencies.
    """

    # Enable auto-wiring for better compatibility with FastAPI's dependency injection
    wiring_config = containers.WiringConfiguration(
        modules=[
            "src.base.dependencies.rate_limiter_dependencies",
            "src.base.dependencies.jwt_dependencies",
        ],
        auto_wire=True,
    )
