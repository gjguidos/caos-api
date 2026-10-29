"""Enki and Thoth Agent Souls: registration, serialization, resolution and V2 assembly.

Souls are character only. They are registered in the existing personality registry,
carry no capability or authority, and are assembled by the V2 prompt builder.
"""

from __future__ import annotations

import json

import pytest

from src.domains.agentverse.agents.mixins.prompt import PromptMixin
from src.domains.agentverse.entities.agent_soul_protocol_parts.agent_soul_protocol import (
    AgentSoulProtocol,
)
from src.domains.agentverse.personalities import enki, thoth  # noqa: F401 - registration
from src.domains.agentverse.personalities.utils.resolve_personality import resolve_personality
from src.domains.agentverse.registries import personality_registry_instance

GROUNDING = (
    "Never claim divine, supernatural or real-world powers, or memories of ancient events",
    "Use myth as metaphor, not as fact",
)
TRAITS = {
    "enki": ("Creator", "Architect", "Sage", "Mentor"),
    "thoth": ("Scribe", "Scholar", "Investigator", "Philosopher"),
}


@pytest.mark.parametrize("key", ["enki", "thoth"])
def test_registered_under_canonical_keys_as_global_souls(key: str) -> None:
    soul_class = personality_registry_instance.get(key)
    assert issubclass(soul_class, AgentSoulProtocol)
    info = personality_registry_instance.get_info(key)
    assert info is not None and info.metadata["ownership"] == "global"


@pytest.mark.parametrize("key", ["enki", "thoth"])
def test_resolution_yields_the_distinct_soul_not_the_default(key: str) -> None:
    soul = resolve_personality(key)
    profile = soul.basic_profile
    assert profile.name == key.capitalize()
    assert tuple(profile.archetype) == TRAITS[key]
    assert type(soul) is personality_registry_instance.get(key)
    assert resolve_personality("enki").basic_profile != resolve_personality("thoth").basic_profile


@pytest.mark.parametrize("key", ["enki", "thoth"])
def test_serialization_round_trips_and_minimal_dump_is_json(key: str) -> None:
    soul = resolve_personality(key)
    restored = type(soul).model_validate(soul.model_dump())
    assert restored.basic_profile == soul.basic_profile
    assert restored.identity_profile == soul.identity_profile
    minimal = soul.minimal_dump()
    json.dumps(minimal, default=str)
    assert {"basic_profile", "cognitive_profile", "identity_profile"} <= set(minimal)


@pytest.mark.parametrize("key", ["enki", "thoth"])
def test_souls_are_grounded_and_grant_nothing(key: str) -> None:
    soul = resolve_personality(key)
    principles = soul.basic_profile.key_principles or []
    assert set(GROUNDING) <= set(principles)
    assert "not the deity" in (soul.basic_profile.description or "")
    assert "Inspired by" in (soul.identity_profile.origin or "")
    # Character never expresses authority: no grants, tools or capabilities.
    dumped = json.dumps(soul.model_dump(), default=str).lower()
    for field in ('"grants"', '"tools"', '"capabilities"', '"invoke"'):
        assert field not in dumped


def test_thoth_distinguishes_evidence_and_enki_guides_composition() -> None:
    thoth_principles = resolve_personality("thoth").basic_profile.key_principles or []
    enki_principles = resolve_personality("enki").basic_profile.key_principles or []
    assert "Distinguish fact, inference and uncertainty explicitly" in thoth_principles
    assert "Never invent sources, quotations, data or records" in thoth_principles
    assert any("human review" in p for p in enki_principles)


@pytest.mark.parametrize("key", ["enki", "thoth"])
def test_v2_prompt_builder_assembles_the_soul(key: str) -> None:
    content = PromptMixin().build_system_prompt_from_soul(resolve_personality(key))["content"]
    profile = resolve_personality(key).basic_profile
    assert f"You are **{profile.name}**" in content
    assert profile.reasoning_style in content
    for principle in profile.key_principles or []:
        assert principle in content
