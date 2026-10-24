import importlib
import sys

import pytest

from src.domains.agentverse.registries.base import Registry
from src.domains.agentverse.registries.synaptic_registry import SynapticBinding, SynapticRegistry
from src.domains.agentverse.services.registry_service import RegistryService


async def handler(event=None, context=None):
    raise AssertionError("Registration and lookup must not execute handlers")


@pytest.fixture
def registry():
    previous = Registry._instances.copy()
    instance = SynapticRegistry()
    yield instance
    Registry._instances.clear()
    Registry._instances.update(previous)


def test_decorator_lookup_metadata_and_catalog(registry):
    assert registry.get_info("prompt.build") is None  # negative info cache
    decorated = registry.subscribe("prompt.build", provider="tool.prompt")(handler)
    assert decorated is handler
    binding = registry.resolve("prompt.build")
    assert binding == SynapticBinding("prompt.build", "tool.prompt", handler)
    assert registry.get("prompt.build", "1.0.0") is binding
    assert registry.get_info("prompt.build").metadata == {
        "signal": "prompt.build",
        "provider": "tool.prompt",
    }
    assert registry.get_info("prompt.build").key == "prompt.build"
    assert registry.keys() == registry.list() == ["prompt.build"]
    assert registry.values() == [binding]
    assert registry.items()[0].key == "prompt.build"
    assert registry.list(include_metadata=True)[0]["key"] == "prompt.build"
    assert Registry.catalog()["synaptic"]["registered_types"] == ["prompt.build"]
    assert registry.resolve_key("prompt.build", kind="signal") == "prompt.build"
    assert "prompt.build" in registry
    assert "prompt_build" not in registry


def test_addresses_that_canonicalize_identically_remain_distinct(registry):
    for signal in (
        "context.delta.build",
        "context_delta.build",
        "context.delta_build",
        "a.bc",
        "ab.c",
    ):
        registry.subscribe(signal, provider="tool.context")(handler)
        assert registry.resolve(signal).signal == signal
    assert len(registry) == 5
    assert len({item.key for item in registry.items()}) == 5


@pytest.mark.parametrize(
    "signal",
    [
        "",
        "prompt_build",
        "Prompt.build",
        " prompt.build",
        "prompt.build ",
        "prompt..build",
        "prompt.*",
        None,
        42,
    ],
)
def test_invalid_signal_rejected(registry, signal):
    with pytest.raises(ValueError):
        registry.subscribe(signal, provider="tool.prompt")(handler)
    assert not registry


@pytest.mark.parametrize("provider", ["", " ", " tool.prompt", "tool.prompt ", None, 42])
def test_invalid_provider_rejected(registry, provider):
    with pytest.raises(ValueError):
        registry.subscribe("prompt.build", provider=provider)(handler)
    assert not registry


@pytest.mark.parametrize("version", ["", " ", " 1.0.0", None, 1])
def test_invalid_version_rejected(registry, version):
    with pytest.raises(ValueError):
        registry.subscribe("prompt.build", provider="tool.prompt", version=version)(handler)


@pytest.mark.parametrize("invalid_handler", [None, 42, lambda: None])
def test_invalid_handler_rejected(registry, invalid_handler):
    with pytest.raises(TypeError):
        registry.subscribe("prompt.build", provider="tool.prompt")(invalid_handler)


def test_conflicts_preserve_binding_and_caches(registry):
    registry.subscribe("prompt.build", provider="tool.prompt")(handler)
    original = registry.get("prompt.build")
    info = registry.get_info("prompt.build")

    async def unrelated():
        pass

    for provider, replacement in (("other.provider", handler), ("tool.prompt", unrelated)):
        with pytest.raises(ValueError, match="Conflicting"):
            registry.subscribe("prompt.build", provider=provider)(replacement)
    assert registry.get("prompt.build") is original
    assert registry.get_info("prompt.build") is info
    assert registry.registration_count == 1


def test_identical_registration_is_idempotent(registry):
    subscribe = registry.subscribe("prompt.build", provider="tool.prompt")
    subscribe(handler)
    subscribe(handler)
    assert registry.registration_count == len(registry) == 1
    assert registry.get("prompt.build").handler is handler


def test_registry_service_discovery_round_trip(registry):
    registry.subscribe("prompt.build", provider="tool.prompt")(handler)
    service = RegistryService({"synaptic": registry})
    entry = service.list("synaptic", include_metadata=True)[0]
    info = service.get_info("synaptic", entry["key"])
    assert info.key == "prompt.build"
    assert service.get_component("synaptic", info.key).handler is handler
    assert service.get_metrics("synaptic")["component_count"] == 1


def test_actual_module_reload_refreshes_handler_and_caches(registry, tmp_path, monkeypatch):
    module_name = "synaptic_reload_fixture"
    (tmp_path / f"{module_name}.py").write_text("async def build(event, context):\n    return 1\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    try:
        module = importlib.import_module(module_name)
        registry.subscribe("prompt.build", provider="tool.prompt")(module.build)
        original = registry.get("prompt.build", "1.0.0")
        info = registry.get_info("prompt.build")
        importlib.reload(module)
        registry.subscribe("prompt.build", provider="tool.prompt", version="2.0.0")(module.build)
        refreshed = registry.get("prompt.build", "2.0.0")
        assert refreshed.handler is module.build
        assert refreshed.handler is not original.handler
        assert registry.get_info("prompt.build") is not info
        assert registry.get_info("prompt.build").version == "2.0.0"
        with pytest.raises(KeyError):
            registry.get("prompt.build", "1.0.0")
        assert registry.registration_count == len(registry) == 1
    finally:
        sys.modules.pop(module_name, None)


def test_legacy_import_alias_refresh(registry):
    async def original():
        pass

    async def refreshed():
        pass

    original.__module__ = "src.domains.example"
    refreshed.__module__ = "domains.example"
    refreshed.__qualname__ = original.__qualname__
    registry.subscribe("prompt.build", provider="tool.prompt")(original)
    registry.subscribe("prompt.build", provider="tool.prompt")(refreshed)
    assert registry.get("prompt.build").handler is refreshed


def test_direct_registration_cannot_bypass_validation_or_conflicts(registry):
    registry.validate_components = False
    binding = SynapticBinding("prompt.build", "tool.prompt", handler)
    assert registry.register("Prompt builder", key="prompt.build", component=binding) is binding
    with pytest.raises(ValueError, match="Conflicting"):
        registry.register(
            "prompt.build", component=SynapticBinding("prompt.build", "other.provider", handler)
        )
    with pytest.raises(TypeError):
        registry.register("prompt.build", component=object())
    with pytest.raises(ValueError, match="key/version"):
        registry.register("wrong.key", component=binding)
    with pytest.raises(ValueError, match="key/version"):
        registry.register("prompt.build", version="2.0.0", component=binding)
    with pytest.raises(ValueError, match="metadata"):
        registry.register("prompt.build", metadata={"provider": "spoof"}, component=binding)


def test_freeze_blocks_all_mutation_paths_including_pending_decorators(registry):
    registry.subscribe("prompt.build", provider="tool.prompt")(handler)
    pending = registry.subscribe("context.build", provider="tool.context")
    direct_pending = registry.register("context.build")
    registry.freeze()
    registry.freeze()
    assert registry.frozen
    operations = [
        lambda: registry.subscribe("prompt.build", provider="tool.prompt"),
        lambda: pending(handler),
        lambda: direct_pending(SynapticBinding("context.build", "tool.context", handler)),
        lambda: registry.register(
            "context.build", component=SynapticBinding("context.build", "tool.context", handler)
        ),
        lambda: registry.unregister("prompt.build"),
    ]
    for operation in operations:
        with pytest.raises(RuntimeError, match="frozen"):
            operation()
    assert registry.get("prompt.build").handler is handler
    assert len(registry) == 1


def test_unknown_lookup_and_exact_unregister(registry):
    with pytest.raises(KeyError):
        registry.resolve("unknown.signal")
    with pytest.raises(ValueError):
        registry.resolve_key("unknown.signal", kind="signal")
    registry.subscribe("prompt.build", provider="tool.prompt")(handler)
    registry.get("prompt.build")
    registry.get_info("prompt.build")
    registry.unregister("prompt.build")
    assert registry.get_info("prompt.build") is None
    with pytest.raises(KeyError):
        registry.get("prompt.build")
    with pytest.raises(KeyError):
        registry.unregister("prompt.build")


def test_domain_singleton_exports_and_catalog():
    from src.domains.agentverse.registries import get_registry, synaptic_registry_instance
    from src.domains.agentverse.registries.utils.register_registry import _REGISTERED_REGISTRIES

    assert get_registry("synaptic") is synaptic_registry_instance
    assert isinstance(synaptic_registry_instance, SynapticRegistry)
    assert _REGISTERED_REGISTRIES["synaptic"]["version"] == "0.1.0"


def test_generic_registry_keeps_canonicalization_and_last_write_wins():
    previous = Registry._instances.copy()
    try:
        generic = Registry("test_generic")
        generic.register("Prompt.Build", component=1)
        generic.register("Prompt.Build", component=2)
        assert generic.get("promptbuild") == 2
    finally:
        Registry._instances.clear()
        Registry._instances.update(previous)
