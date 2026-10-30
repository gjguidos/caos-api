"""Production startup binding checks, with no service/network I/O."""

import json
from dataclasses import replace

import pytest

from src.domains.agentverse.agents.lifecycle.tests.test_genesis_startup_wiring import (
    LOCAL_POLICY,
    start_production_app,
)
from src.domains.agentverse.manuscripts import capability
from src.domains.agentverse.tools.manuscript_tool import validate_manuscript_bindings


@pytest.mark.asyncio
async def test_enabled_startup_validates_all_six_exact_bindings(monkeypatch):
    _, registry = await start_production_app(monkeypatch, LOCAL_POLICY)
    validate_manuscript_bindings(registry)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["missing", "version", "provider", "conflict"])
async def test_enabled_startup_fails_closed_on_broken_provider_registration(monkeypatch, failure):
    original = capability.register_manuscript_capability

    def broken(registry, provider):
        if failure == "missing":
            return []
        for op in capability.OPERATIONS:
            binding = capability.BINDINGS[op]
            changed = replace(
                binding,
                provider="other.provider"
                if failure in {"provider", "conflict"}
                else binding.provider,
                version="2.0.0" if failure == "version" else binding.version,
            )
            registry.subscribe(changed.signal, provider=changed.provider, version=changed.version)(
                provider.handler(op)
            )
        if failure == "conflict":
            original(registry, provider)

    monkeypatch.setattr(capability, "register_manuscript_capability", broken)
    with pytest.raises(ValueError, match="Manuscript binding|Conflicting synaptic registration"):
        await start_production_app(monkeypatch, LOCAL_POLICY)


@pytest.mark.asyncio
async def test_catalog_descriptor_does_not_require_execution_provisioning(monkeypatch, tmp_path):
    config = json.loads(LOCAL_POLICY.read_text())
    for grant in config["grants"]:
        grant["invoke"] = [s for s in grant["invoke"] if s not in capability.SIGNALS.values()]
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps(config))
    _, registry = await start_production_app(monkeypatch, policy)
    assert all(signal not in registry for signal in capability.SIGNALS.values())
    from src.domains.agentverse.registries import tool_registry_instance

    assert tool_registry_instance.get_info("manuscript") is not None


@pytest.mark.asyncio
async def test_enabled_policy_without_provider_gateway_fails_startup(monkeypatch, tmp_path):
    config = json.loads(LOCAL_POLICY.read_text())
    config["validation_gateways"] = []
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps(config))
    with pytest.raises(ValueError, match="Missing exact Manuscript binding"):
        await start_production_app(monkeypatch, policy)
