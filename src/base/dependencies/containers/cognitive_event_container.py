from dependency_injector import containers, providers

from src.base.events.v2.policy import CognitiveEventPolicy
from src.base.events.v2.registry import CognitiveEventRegistry
from src.base.events.v2.runtime import CognitiveEventRuntime


class CognitiveEventContainer(containers.DeclarativeContainer):
    """CAOSA cognitive event infrastructure."""

    messaging = providers.DependenciesContainer()

    config = providers.Configuration()

    event_registry = providers.Singleton(
        CognitiveEventRegistry,
    )

    event_policy = providers.Singleton(
        CognitiveEventPolicy,
        grants={},
    )

    event_runtime = providers.Singleton(
        CognitiveEventRuntime,
        messaging=messaging.rabbitmq_service,
        registry=event_registry,
        policy=event_policy,
        signing_key=config.signing_key,
    )