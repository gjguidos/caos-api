from dependency_injector import containers, providers

from src.base.dependencies.containers import (
    AuthzContainer,
    AzureSearchContainer,
    BlobStorageContainer,
    ChromaDBContainer,
    DatabaseContainer,
    EventContainer,
    HttpContainer,
    LLMContainer,
    OperationsContainer,
    RateLimiterContainer,
    RedisContainer,
    ServiceContainer,
    SettingsContainer,
    SocketContainer,
)


class DomainsHolder:
    pass


class Container(containers.DeclarativeContainer):
    """
    Root container that merges all domain-specific containers.
    """

    # Sub-containers
    wiring_config = containers.WiringConfiguration(
        modules=[
            "src.base.dependencies.rate_limiter_dependencies",
            "src.base.dependencies.jwt_dependencies",
            "src.base.config.config",
        ],
        auto_wire=True,
    )
    authz = providers.Container(AuthzContainer)
    blob_storage = providers.Container(BlobStorageContainer)
    database = providers.Container(DatabaseContainer)
    events = providers.Container(EventContainer)
    operations = providers.Container(OperationsContainer)
    services = providers.Container(ServiceContainer)
    redis = providers.Container(RedisContainer)
    rate_limiter = providers.Container(RateLimiterContainer)
    settings_container = providers.Container(SettingsContainer)
    chromadb = providers.Container(ChromaDBContainer)
    azure_search = providers.Container(AzureSearchContainer)
    socket = providers.Container(SocketContainer)
    llm_container = providers.Container(LLMContainer)
    http = providers.Container(HttpContainer)
    domains = providers.Object(DomainsHolder())


# Create a container instance for the application
container = Container()
