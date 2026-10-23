import hashlib
import hmac
import importlib.util
import json
import socket
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from dependency_injector import containers, providers
from pydantic import SecretStr, ValidationError

from src.base.config.cognitive_event_config import CognitiveEventConfig
from src.base.events.v2.contracts import EventPermissionError
from src.base.events.v2.policy import EventPrincipal
from src.base.events.v2.runtime import CognitiveEventRuntime
from src.base.events.v2.tests.test_runtime_signatures import EVENT_TYPE, event_setup

# Re-export the existing fixture so pytest can discover it in this module.
__all__ = ["event_setup"]


@pytest.fixture
def root_container_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Callable[[CognitiveEventConfig], containers.DynamicContainer]]:
    """Load actual root/cognitive/messaging wiring, isolating unrelated imports.

    Root settings are captured at class definition time, so each configuration
    requires fresh source loading. No application resources are initialized.
    """
    dependencies = Path(__file__).parents[3] / "dependencies"
    roots: list[containers.DynamicContainer] = []

    def forbid_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("Root wiring tests must not contact external infrastructure")

    monkeypatch.setattr(socket, "create_connection", forbid_network)
    monkeypatch.setattr(socket, "getaddrinfo", forbid_network)
    monkeypatch.setattr(socket.socket, "connect", forbid_network)
    monkeypatch.setattr(socket.socket, "connect_ex", forbid_network)

    class ExternalContainer(containers.DeclarativeContainer):
        # Only ChromaDB receives this dependency from the actual root definition.
        llm_container = providers.DependenciesContainer()

    def stub(name: str, **attrs: object) -> ModuleType:
        module = ModuleType(name)
        module.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, module)
        return module

    def load(name: str, path: Path) -> ModuleType:
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        return module

    def build(config: CognitiveEventConfig) -> containers.DynamicContainer:
        stub(
            "src.base.config.config",
            settings=SimpleNamespace(
                cognitive_event=config,
                messaging=SimpleNamespace(url="amqp://unused"),
            ),
        )
        # These auto-wiring targets otherwise import the canonical root again.
        for target in ("rate_limiter_dependencies", "jwt_dependencies", "version_dependencies"):
            stub(f"src.base.dependencies.{target}")
        barrel = stub("src.base.dependencies.containers")
        for name in (
            "AuthzContainer",
            "AzureSearchContainer",
            "BlobStorageContainer",
            "ChromaDBContainer",
            "DatabaseContainer",
            "EventContainer",
            "HttpContainer",
            "LLMContainer",
            "OperationsContainer",
            "RateLimiterContainer",
            "RedisContainer",
            "ServiceContainer",
            "SettingsContainer",
            "SocketContainer",
        ):
            setattr(barrel, name, ExternalContainer)
        cognitive = load(
            "_caos_test_cognitive_container",
            dependencies / "containers" / "cognitive_event_container.py",
        )
        messaging = load(
            "_caos_test_messaging_container",
            dependencies / "containers" / "messaging_container.py",
        )
        barrel.CognitiveEventContainer = cognitive.CognitiveEventContainer
        barrel.MessagingContainer = messaging.MessagingContainer
        root_module = load("_caos_test_root_container", dependencies / "di_container.py")
        root = root_module.container
        assert root.declarative_parent is root_module.Container
        roots.append(root)
        return root

    yield build
    for root in reversed(roots):
        root.unwire()


@pytest.mark.asyncio
@pytest.mark.parametrize("secret", ["x" * 32, "é" * 16], ids=["ascii-32-bytes", "utf8-32-bytes"])
async def test_root_encodes_secret_and_resolves_signing_runtime(
    root_container_factory: Callable[[CognitiveEventConfig], containers.DynamicContainer],
    event_setup: tuple[CognitiveEventRuntime, AsyncMock, AsyncMock],
    secret: str,
) -> None:
    config = CognitiveEventConfig(signing_key=SecretStr(secret), _env_file=None)
    assert isinstance(config.signing_key, SecretStr)
    root = root_container_factory(config)
    prototype, messaging, handler = event_setup
    with (
        root.messaging.rabbitmq_service.override(providers.Object(messaging)),
        root.cognitive_event.event_registry.override(providers.Object(prototype._registry)),
        root.cognitive_event.event_policy.override(providers.Object(prototype._policy)),
    ):
        # Do not override the key or runtime: both must resolve through real root wiring.
        runtime = root.cognitive_event.event_runtime()
        assert isinstance(runtime, CognitiveEventRuntime)
        assert runtime is root.cognitive_event.event_runtime()
        assert runtime._messaging is messaging
        assert isinstance(runtime._key, bytes)
        assert not isinstance(runtime._key, str)
        assert runtime._key == secret.encode("utf-8")
        assert len(runtime._key) == 32
        await runtime.start()
        try:
            event = await runtime.bind(EventPrincipal("publisher", "tenant")).emit(
                EVENT_TYPE, {"text": "café"}, correlation_id="root-wiring"
            )
            published = messaging.publish_event.await_args.kwargs
            canonical = json.dumps(
                event.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            assert (
                published["headers"]["caos-signature"]
                == hmac.new(secret.encode("utf-8"), canonical, hashlib.sha256).hexdigest()
            )
            callback = messaging.subscribe.await_args.args[1]
            await callback(
                SimpleNamespace(
                    body=json.dumps(published["message"]).encode("utf-8"),
                    headers=published["headers"],
                )
            )
            handler.assert_awaited_once_with(event)
            handler.reset_mock()
            published["message"]["payload"]["text"] = "tampered"
            with pytest.raises(EventPermissionError, match="Invalid or missing event signature"):
                await callback(
                    SimpleNamespace(
                        body=json.dumps(published["message"]).encode("utf-8"),
                        headers=published["headers"],
                    )
                )
            handler.assert_not_awaited()
        finally:
            await runtime.stop()


@pytest.mark.parametrize("secret", ["", "x" * 31, "é" * 15], ids=["empty", "ascii-31", "utf8-30"])
def test_root_rejects_short_encoded_key(
    root_container_factory: Callable[[CognitiveEventConfig], containers.DynamicContainer],
    event_setup: tuple[CognitiveEventRuntime, AsyncMock, AsyncMock],
    secret: str,
) -> None:
    root = root_container_factory(
        CognitiveEventConfig(signing_key=SecretStr(secret), _env_file=None)
    )
    _, messaging, _ = event_setup
    with (
        root.messaging.rabbitmq_service.override(providers.Object(messaging)),
        pytest.raises(ValueError, match="signing_key must be >= 32 bytes"),
    ):
        root.cognitive_event.event_runtime()
    messaging.publish_event.assert_not_awaited()


@pytest.mark.parametrize("invalid_key", [None, 123])
def test_invalid_secret_configuration_fails_validation(invalid_key: object) -> None:
    with pytest.raises(ValidationError) as caught:
        CognitiveEventConfig(signing_key=invalid_key, _env_file=None)
    assert caught.value.errors()[0]["loc"] == ("signing_key",)


def test_missing_secret_configuration_fails_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("COGNITIVE_EVENT_SIGNING_KEY", raising=False)
    monkeypatch.delenv("cognitive_event_signing_key", raising=False)
    with pytest.raises(ValidationError) as caught:
        CognitiveEventConfig(_env_file=None)
    assert caught.value.errors()[0]["loc"] == ("signing_key",)
    assert caught.value.errors()[0]["type"] == "missing"
