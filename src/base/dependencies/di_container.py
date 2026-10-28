from dependency_injector import containers, providers

from src.base.config.config import settings
from src.base.dependencies.containers import (
    AuthzContainer,
    AzureSearchContainer,
    BlobStorageContainer,
    ChromaDBContainer,
    CognitiveEventContainer,
    DatabaseContainer,
    EventContainer,
    HttpContainer,
    LLMContainer,
    MessagingContainer,
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
            "src.base.dependencies.version_dependencies",
            "src.base.config.config",
        ],
        auto_wire=True,
    )
    authz = providers.Container(AuthzContainer)
    blob_storage = providers.Container(BlobStorageContainer)
    database = providers.Container(DatabaseContainer)
    events = providers.Container(EventContainer)
    operations = providers.Container(
        OperationsContainer,
        mongodb_repository=database.mongodb_repository,
    )
    services = providers.Container(ServiceContainer)
    redis = providers.Container(RedisContainer)
    rate_limiter = providers.Container(RateLimiterContainer)
    settings_container = providers.Container(SettingsContainer)
    azure_search = providers.Container(AzureSearchContainer)
    socket = providers.Container(SocketContainer)
    llm_container = providers.Container(LLMContainer)
    chromadb = providers.Container(
        ChromaDBContainer,
        llm_container=llm_container,
    )
    messaging = providers.Container(MessagingContainer)
    cognitive_event = providers.Container(
        CognitiveEventContainer,
        messaging=messaging,
        config={
            "signing_key": (
                settings.cognitive_event.signing_key.get_secret_value().encode("utf-8")
            ),
        },
    )
    http = providers.Container(HttpContainer)
    domains = providers.Object(DomainsHolder())


# Create a container instance for the application
container = Container()
