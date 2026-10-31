"""Production startup: the Reasoning Tool descriptor is independent of provider enablement."""

import json

import pytest

from src.domains.agentverse.agents.lifecycle.tests.test_genesis_startup_wiring import (
    LOCAL_POLICY,
    start_production_app,
)
from src.domains.agentverse.reasoning.capability import SIGNAL
from src.domains.agentverse.registries import tool_registry_instance
from src.domains.agentverse.tools.reasoning_tool import validate_reasoning_tool


@pytest.mark.asyncio
async def test_enabled_startup_validates_the_tool_declaration_and_binding(monkeypatch):
    _, registry = await start_production_app(monkeypatch, LOCAL_POLICY)
    validate_reasoning_tool(registry)


@pytest.mark.asyncio
async def test_descriptor_does_not_require_or_imply_execution(monkeypatch, tmp_path):
    config = json.loads(LOCAL_POLICY.read_text())
    for grant in config["grants"]:
        grant["invoke"] = [s for s in grant["invoke"] if s != SIGNAL]
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps(config))
    _, registry = await start_production_app(monkeypatch, policy)
    assert SIGNAL not in registry
    assert tool_registry_instance.get_info("reasoning") is not None


@pytest.mark.asyncio
async def test_enabled_startup_fails_closed_without_the_tool_declaration(monkeypatch):
    original = tool_registry_instance.get_info

    def missing(key, *args, **kwargs):
        return None if key == "reasoning" else original(key, *args, **kwargs)

    monkeypatch.setattr(tool_registry_instance, "get_info", missing)
    with pytest.raises(ValueError, match="Reasoning Tool registration is missing"):
        await start_production_app(monkeypatch, LOCAL_POLICY)
