"""Tool declarations survive registration and runtime expansion without granting access."""

from __future__ import annotations

from typing import ClassVar

import pytest
from pydantic import ValidationError

from src.domains.agentverse.agents import factory as factory_module
from src.domains.agentverse.agents.factory import AgentFactory
from src.domains.agentverse.entities.resource_exposure import classify_resource
from src.domains.agentverse.registries.base import Registry, RegistryItem
from src.domains.agentverse.registries.tool_registry import ToolRegistry
from src.domains.agentverse.tools.base import BaseTool
from src.domains.agentverse.tools.spec import ToolSpec


class ExampleTool(BaseTool):
    name = "Example"
    version = "1.2.0"
    parameters: ClassVar[dict] = {}
    default_config: ClassVar[dict] = {"class_default": True}


@pytest.fixture
def registry(monkeypatch):
    # Temporary registries must not replace the process-global catalog singleton.
    monkeypatch.setattr(Registry, "_instances", dict(Registry._instances))
    return ToolRegistry()


def declarations():
    return {
        "scope": "system",
        "ui": {"visibility": "hidden", "label": "Private"},
        "tags": ["system", "context"],
        "config": {"temperature": 0.4},
        "outputs": [{"key": "answer", "label": "Answer", "type": "string", "path": "answer"}],
        "supports": {"system_only": True, "files": {"max_count": 2}},
        "synaptic": {
            "capabilities": [
                {"signal": "example.context", "access": "system", "trusted_lines": ["example.read"]}
            ]
        },
    }


def test_top_level_system_tool_is_not_public(registry):
    registry.bind_spec(ToolSpec(key="private", name="Private", **declarations()))(ExampleTool)
    assert registry.list_public() == []
    info = registry.get_info("private")
    assert info.exposure == "internal"
    for field, value in declarations().items():
        assert info.metadata[field] == (sorted(value) if field == "tags" else value)


def test_top_level_and_metadata_authored_specs_are_equivalent(registry):
    values = declarations()
    registry.bind_spec(ToolSpec(key="example", name="Example", **values))(ExampleTool)
    first = registry.get_info("example").metadata
    first_catalog = registry.list_public(include_system=True, include_hidden=True)
    registry.bind_spec(ToolSpec(key="example", name="Example", metadata=values))(ExampleTool)
    second = registry.get_info("example").metadata
    assert set(second["tags"]) == set(first["tags"])
    assert {k: v for k, v in second.items() if k != "tags"} == {
        k: v for k, v in first.items() if k != "tags"
    }
    second_catalog = registry.list_public(include_system=True, include_hidden=True)
    # Catalog tags are a set projection; iteration order is not a contract.
    assert [{**row, "tags": sorted(row["tags"])} for row in second_catalog] == [
        {**row, "tags": sorted(row["tags"])} for row in first_catalog
    ]


def test_explicit_fields_win_without_rewriting_legacy_tags(registry):
    registry.bind_spec(
        ToolSpec(
            key="example",
            name="Example",
            scope="workflow",
            tags=["New Tag"],
            ui={"label": "Top"},
            metadata={
                "scope": "system",
                "tags": ["Legacy-Tag"],
                "ui": {"label": "Legacy"},
                "custom": 42,
            },
        )
    )(ExampleTool)
    meta = registry.get_info("example").metadata
    assert meta["scope"] == "workflow"
    assert meta["tags"] == ["new_tag"]
    assert meta["ui"] == {"label": "Top"}
    assert meta["custom"] == 42
    registry.bind_spec(
        ToolSpec(
            key="example",
            name="Example",
            metadata={"tags": ["Legacy-Tag"], "ui": {"label": "Legacy"}},
        )
    )(ExampleTool)
    assert registry.get_info("example").metadata["tags"] == ["Legacy-Tag"]


@pytest.mark.parametrize(
    "legacy",
    [
        {"tags": ["system"]},
        {"scope": "system"},
        {"config": {"_scope": "system"}},
        {"supports": {"system_only": True}},
        {"is_system": True},
        {"tags": ["builtin"]},
        {"is_builtin": True},
        {"config": {"_builtin": True}},
        {"visibility": "hidden"},
        {"ui": {"visibility": "hidden"}},
        {"config": {"_hidden": True}},
        {"hidden": True},
    ],
)
def test_legacy_restrictions_survive_public_exposure(registry, legacy):
    meta = {"exposure": "public", **legacy}
    registry.register_tool(key="example", name="Example", metadata=meta)(ExampleTool)
    assert registry.list_public() == []
    assert classify_resource(meta).exposure == "internal"
    assert (
        len(registry.list_public(include_system=True, include_builtin=True, include_hidden=True))
        == 1
    )


def test_standard_exposure_is_independent_of_invocation_scope(registry):
    registry.bind_spec(ToolSpec(key="example", name="Example", exposure="internal", scope="agent"))(
        ExampleTool
    )
    assert registry.get_info("example").metadata["scope"] == "agent"
    assert (
        registry.list_public(include_system=True, include_builtin=True, include_hidden=True) == []
    )
    with pytest.raises(ValidationError):
        ToolSpec(name="Bad", exposure="invalid")
    with pytest.raises(ValueError, match="Invalid resource exposure"):
        registry.register_tool(key="bad", name="Bad", metadata={"exposure": "invalid"})(ExampleTool)
    assert "bad" not in registry


def test_default_config_and_metadata_items_contract_are_preserved(registry):
    registry.bind_spec(ToolSpec(key="example", name="Example"))(ExampleTool)
    assert registry.get_info("example").metadata["config"] == ExampleTool.default_config
    assert registry.get_info("example").metadata["default_config"] == ExampleTool.default_config
    items = registry.items()
    assert len(items) == 1
    assert isinstance(items[0], RegistryItem)
    assert registry.values() == [ExampleTool]


def test_factory_preserves_declarations_and_isolates_config_overrides(registry, monkeypatch):
    values = declarations()
    registry.bind_spec(
        ToolSpec(
            key="example",
            name="Example",
            exposure="internal",
            description="Details",
            version="2.0.0",
            **values,
        )
    )(ExampleTool)
    monkeypatch.setattr(factory_module, "tool_registry", registry)
    before = registry.get_info("example").model_dump()
    spec = AgentFactory()._tool_spec_for(
        "example",
        {"temperature": 0.9, "synaptic": {"forged": True}, "exposure": "public"},
        scope="workflow",
    )
    assert spec.scope == "workflow"
    assert spec.exposure == "internal"
    assert spec.version == "2.0.0"
    assert spec.description == "Details"
    assert spec.tags == sorted(values["tags"])
    assert spec.ui == values["ui"]
    assert spec.supports == values["supports"]
    assert [output.model_dump() for output in spec.outputs] == values["outputs"]
    assert spec.synaptic.model_dump() == values["synaptic"]
    assert spec.config["temperature"] == 0.9
    assert spec.config["class_default"] is True
    assert registry.get_info("example").model_dump() == before


def test_tag_filters_and_disabled_registration_still_work(registry):
    registry.bind_spec(ToolSpec(key="example", name="Example", tags=["search"]))(ExampleTool)
    assert len(registry.list_public(allowed_tags=["SEARCH"])) == 1
    assert registry.list_public(blocked_tags=["search"]) == []
    registry.bind_spec(ToolSpec(key="disabled", name="Disabled", enabled=False))(ExampleTool)
    assert "disabled" not in registry


def test_legacy_custom_metadata_is_not_forced_through_json_serialization(registry):
    custom = object()
    registry.bind_spec(ToolSpec(key="example", name="Example", metadata={"custom": custom}))(
        ExampleTool
    )
    assert registry.get_info("example").metadata["custom"] is custom
