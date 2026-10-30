"""MANUSCRIPT-0.1B: registration/configuration describe availability, never authority."""

import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from src.base.entities.user import User
from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.agents.factory import AgentFactory
from src.domains.agentverse.manuscripts.capability import OPERATIONS, PROVIDER, SIGNALS, VERSION
from src.domains.agentverse.registries import tool_registry_instance
from src.domains.agentverse.registries.base import Registry
from src.domains.agentverse.registries.synaptic_registry import SynapticBinding, SynapticRegistry
from src.domains.agentverse.registries.tool_registry import ToolRegistry
from src.domains.agentverse.services.registry_service import RegistryService
from src.domains.agentverse.tools.base import BaseTool
from src.domains.agentverse.tools.manuscript_tool import (
    ManuscriptTool,
    validate_manuscript_bindings,
)
from src.domains.agentverse.tools.manuscript_tool.spec import ManuscriptToolConfig, manuscript_tool
from src.domains.agentverse.tools.spec import AgentToolRef, SynapticCapabilitySpec
from src.domains.agentverse.tools.tool_manager_v1.registry_adapter import prepare_factories
from src.domains.agentverse.tools.tool_manager_v1.routing import route_for_v1
from src.domains.agentverse.tools.validation_tool.agent_validator import AgentDefinitionValidator


@pytest.fixture
def tools(monkeypatch):
    monkeypatch.setattr(Registry, "_instances", dict(Registry._instances))
    registry = ToolRegistry()
    registry.bind_spec(manuscript_tool)(ManuscriptTool)
    return registry


async def handler(*args):
    raise AssertionError("Binding validation must never execute a handler")


def bindings():
    registry = SynapticRegistry()
    for signal in SIGNALS.values():
        registry.subscribe(signal, provider=PROVIDER, version=VERSION)(handler)
    return registry


def test_registered_optional_tool_and_exact_actions(tools):
    registered = tool_registry_instance.get("manuscript", "1.0.0")
    assert issubclass(registered, BaseTool) and registered.key == "manuscript"
    info = tools.get_info("manuscript")
    assert info.version == "1.0.0"
    assert info.metadata["scope"] == "agent"
    assert info.metadata["exposure"] == "public"
    actions = info.metadata["synaptic"]["capabilities"]
    assert {a["signal"] for a in actions} == set(SIGNALS.values())
    assert len(actions) == 6
    for op, action in zip(OPERATIONS, actions, strict=True):
        assert action["version"] == VERSION
        assert action["provider"] == PROVIDER
        assert action["access"] == "granted"
        assert action["trusted_lines"] == []
        assert action["input_contract"] == f"caos.manuscript_session.{op}@1"
    validate_manuscript_bindings(bindings(), tools=tools)
    # Tool identity version is independent; it can never select an action version.
    info.version = "9.0.0"
    validate_manuscript_bindings(bindings(), tools=tools)


def test_legacy_action_serialization_unchanged_and_versions_not_inferred():
    old = {"signal": "legacy.action", "access": "granted", "trusted_lines": []}
    assert SynapticCapabilitySpec(**old).model_dump() == old
    assert SynapticCapabilitySpec(**old).version is None
    with pytest.raises(ValidationError):
        SynapticCapabilitySpec(signal="legacy.action", version="latest")
    with pytest.raises(ValidationError):
        SynapticCapabilitySpec(signal="legacy.action@1.0.0")
    with pytest.raises(ValidationError):
        SynapticCapabilitySpec(signal="legacy.action", provider="forged system.manuscript")


@pytest.mark.parametrize(
    "value",
    ["interview", "interview@latest", "interview@1", "X@1.0.0", "x@01.0.0", 12, None, ["x@1.0.0"]],
)
def test_configuration_requires_an_exact_version(value):
    with pytest.raises(ValidationError):
        ManuscriptToolConfig(default_manuscript=value)


@pytest.mark.parametrize(
    "field",
    [
        "grants",
        "scope",
        "scopes",
        "provider",
        "trusted_lines",
        "tenant_id",
        "agent_id",
        "owner",
        "permit",
        "synaptic",
        "_hidden",
        "enabled",
    ],
)
def test_configuration_cannot_express_authority(field):
    with pytest.raises(ValidationError):
        ManuscriptToolConfig.model_validate({field: "forged"})


def test_config_uses_existing_sparse_agent_reference_and_validator(tools):
    ref = AgentToolRef(key="manuscript", config={"default_manuscript": "other_interview@2.0.0"})
    issues = SimpleNamespace(add=Mock())
    config = AgentDefinitionValidator._tool_config(
        tools.get_info(ref.key), ref.config, "tools.0.config", issues
    )
    assert config == ref.config
    issues.add.assert_not_called()
    assert set(ref.model_dump()) == {"key", "config"}
    assert (
        AgentDefinitionValidator._tool_config(
            tools.get_info(ref.key), {"provider": PROVIDER}, "tools.0.config", issues
        )
        is None
    )
    issues.add.assert_called_once()


def test_requester_catalog_exposes_safe_metadata_not_operative_data(tools):
    service = RegistryService({"tool": tools})
    user = User(id="reader", tenant_id="tenant")
    raw = tools.get_info("manuscript").metadata
    # Unknown operative metadata must never pass through the projection.
    raw["private_definition"] = {"guidance": "PRIVATE_SENTINEL"}
    raw["sessions"] = ["PRIVATE_SENTINEL"]
    raw["credentials"] = "PRIVATE_SENTINEL"
    raw["config"]["secret"] = "PRIVATE_SENTINEL"
    before = copy.deepcopy(raw)
    rows = service.list_for_client("tool", user, include_metadata=True)
    assert len(rows) == 1 and rows[0]["key"] == "manuscript"
    detail = service.get_info_for_client("tool", "manuscript", user).metadata
    text = json.dumps(rows)
    assert not any(v in text for v in (PROVIDER, "manuscript.session.", "PRIVATE_SENTINEL"))
    assert (
        not {"synaptic", "provider", "config", "default_config", "execution_mode", "config_schema"}
        & detail.keys()
    )
    schema = detail["agent_config_schema"]
    assert set(schema["properties"]) == {"default_manuscript"}
    assert schema["properties"]["default_manuscript"]["type"] == "string"
    assert "pattern" in schema["properties"]["default_manuscript"]
    assert raw == before  # Projection cannot mutate the trusted declaration.
    assert service.list_for_client("tool", None) == []


def test_tenant_owned_descriptor_is_not_disclosed_cross_tenant(tools):
    tools.get_info("manuscript").metadata.update(ownership="tenant", tenant_id="tenant")
    service = RegistryService({"tool": tools})
    assert service.list_for_client("tool", User(id="reader", tenant_id="other")) == []
    with pytest.raises(KeyError):
        service.get_info_for_client("tool", "manuscript", User(id="reader", tenant_id="other"))


@pytest.mark.parametrize(
    "failure",
    [
        "missing_tool",
        "missing_action",
        "duplicate_action",
        "missing_provider",
        "missing_version",
        "wrong_provider",
        "wrong_version",
        "trusted_line",
        "input_contract",
        "output_contract",
        "access",
        "missing_binding",
        "binding_provider",
        "binding_version",
    ],
)
def test_startup_rejects_missing_or_mismatched_bindings(tools, failure):
    registry = bindings()
    actions = tools.get_info("manuscript").metadata["synaptic"]["capabilities"]
    first = actions[0]
    if failure == "missing_tool":
        tools.unregister("manuscript")
    elif failure == "missing_action":
        actions.pop()
    elif failure == "duplicate_action":
        actions[-1] = dict(first)
    elif failure in {"missing_provider", "missing_version"}:
        first.pop(failure.removeprefix("missing_"))
    elif failure in {"wrong_provider", "wrong_version"}:
        first[failure.removeprefix("wrong_")] = (
            "other.provider" if failure.endswith("provider") else "2.0.0"
        )
    elif failure == "trusted_line":
        first["trusted_lines"] = ["store.create"]
    elif failure in {"input_contract", "output_contract", "access"}:
        first[failure] = "core" if failure == "access" else "wrong.contract@1"
    else:
        registry.unregister(first["signal"])
        if failure != "missing_binding":
            provider = "other.provider" if failure == "binding_provider" else PROVIDER
            version = "2.0.0" if failure == "binding_version" else VERSION
            registry.register(
                first["signal"],
                version=version,
                component=SynapticBinding(first["signal"], provider, handler, version),
            )
    with pytest.raises(ValueError):
        validate_manuscript_bindings(registry, tools=tools)


@pytest.mark.asyncio
async def test_legacy_v1_is_denied_even_when_attached():
    ref = AgentToolRef(key="manuscript")
    spec = AgentFactory()._ref_to_spec(ref)
    factories = {}
    prepare_factories(factories=factories, tool_anchor_map={}, tool_specs=[spec])
    with pytest.raises(ValueError, match="missing meta.route"):
        route_for_v1(factories, "manuscript")
    tool = factories["manuscript"]["cls"]()
    with pytest.raises(EventPermissionError, match="V1 is denied"):
        await tool.execute(actor={"tenant_id": "forged"})
    with pytest.raises(EventPermissionError):
        await tool._run()


@pytest.mark.asyncio
async def test_attachment_cannot_supply_a_grant():
    tool = ManuscriptTool(AgentToolRef(key="manuscript").config)
    authority = SimpleNamespace(attenuate=AsyncMock(side_effect=EventPermissionError("No grant")))
    exchange = Mock()
    with pytest.raises(EventPermissionError, match="No grant"):
        await tool.invoke(
            authority=authority,
            exchange=exchange,
            principal=object(),
            op="status",
            command={"session_id": "not-authority"},
        )
    exchange.bind.assert_not_called()


@pytest.mark.asyncio
async def test_trusted_adapter_reuses_existing_helper_without_mutating_commands(monkeypatch):
    import src.domains.agentverse.tools.manuscript_tool as module

    invoke = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(module, "invoke_manuscript", invoke)
    tool = ManuscriptTool({"default_manuscript": "other_interview@2.0.0"})
    context = {"authority": object(), "exchange": object(), "principal": object()}
    start = {"contract": "caos.manuscript_session.start", "idempotency_key": "start-key-00000001"}
    await tool.invoke(**context, op="start", command=start)
    assert invoke.call_args.args[-1]["manuscript"] == "other_interview@2.0.0"
    assert "manuscript" not in start
    explicit = {**start, "manuscript": "pinned_interview@1.0.0"}
    await tool.invoke(**context, op="start", command=explicit)
    assert invoke.call_args.args[-1] == explicit
    status = {"session_id": "existing-session"}
    await tool.invoke(**context, op="status", command=status)
    assert invoke.call_args.args[-1] == status
    assert not {"_manuscripts", "repository", "llm", "store", "proposals"} & vars(tool).keys()
