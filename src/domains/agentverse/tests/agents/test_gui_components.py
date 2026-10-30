"""Declarative Agent GUI composition v1: structure, defaults and persistence."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.domains.agentverse.entities.agent import Agent
from src.domains.agentverse.entities.gui_components import (
    GUI_COMPONENT_TYPES,
    MAX_COMPONENTS,
    ChatComponent,
    FormConfig,
    GuiComponents,
    default_gui_components,
    effective_gui_components,
)


def agent(**changes):
    base = {
        "id": "a1",
        "name": "Thoth",
        "system_name": "thoth",
        "type": "chat",
        "prompt": "p",
        "chat_url": "",
        "llm_type": "m",
        "audio_type": "",
        "db_type": None,
        "cache_type": None,
        "knowledge_db_type": None,
        "messaging_type": None,
        "dna_sequence": "d",
    }
    return Agent.model_validate({**base, **changes})


def test_only_chat_and_form_are_supported_and_forms_declare_no_fields() -> None:
    assert GUI_COMPONENT_TYPES == ("chat", "form")
    # A form's config only names its source; Workflows own fields and presentation.
    assert set(FormConfig.model_fields) == {"source"}


def test_legacy_definitions_without_gui_still_load() -> None:
    legacy = agent()
    assert legacy.gui_components == []
    assert "gui_components" in legacy.model_dump()


def test_persisted_gui_is_validated_on_the_agent() -> None:
    with pytest.raises(ValidationError):
        agent(gui_components=[{"type": "chat", "key": "a"}])  # no default
    with pytest.raises(ValidationError):
        agent(
            gui_components=[{"type": "chat", "key": f"k{i}", "default": i == 0} for i in range(9)]
        )
    ok = agent(gui_components=[{"type": "chat", "key": "conversation", "default": True}])
    assert isinstance(ok.gui_components[0], ChatComponent)
    assert ok.model_dump(mode="json")["gui_components"] == [
        {"version": 1, "key": "conversation", "default": True, "type": "chat", "config": {}}
    ]
    assert MAX_COMPONENTS == 8


@pytest.mark.parametrize(
    "agent_type,role,runtime,expected",
    [
        ("chat", "thoth", "v2", [("chat", "conversation", True)]),
        (
            "form",
            "enki",
            "v2",
            [("form", "agent_configuration", True), ("chat", "conversation", False)],
        ),
        ("form", "general", "v2", []),  # no reviewed presentation
        ("chat", "general", "v1", []),  # V1 keeps its existing workspaces
    ],
)
def test_reviewed_defaults_mirror_v2_composition(agent_type, role, runtime, expected) -> None:
    components = default_gui_components(
        agent_type=agent_type, role_name=role, runtime_version=runtime
    )
    assert [(c.type, c.key, c.default) for c in components] == expected
    GuiComponents(components=components)  # defaults satisfy the structural contract


def test_enki_form_presents_its_existing_draft_contract() -> None:
    form = default_gui_components(agent_type="form", role_name="enki", runtime_version="v2")[0]
    assert form.config.source.model_dump() == {
        "kind": "contract",
        "contract": "caos.agent_creation_draft",
        "schema_version": 1,
    }


def test_effective_gui_prefers_persisted_components_over_defaults() -> None:
    legacy_enki = {"type": "form", "role_name": "enki", "runtime_version": "v2"}
    assert [c.key for c in effective_gui_components(legacy_enki)] == [
        "agent_configuration",
        "conversation",
    ]
    declared = {
        **legacy_enki,
        "gui_components": [{"type": "chat", "key": "only_chat", "default": True}],
    }
    assert [c.key for c in effective_gui_components(declared)] == ["only_chat"]
