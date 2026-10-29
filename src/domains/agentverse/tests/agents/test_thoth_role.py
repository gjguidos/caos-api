"""Thoth's dedicated conversational Role: function only, no tools, no authority."""

from __future__ import annotations

import json

from src.domains.agentverse.agents.conversational_composition_v2 import supports_v2_conversation
from src.domains.agentverse.agents.roles.thoth import thoth  # noqa: F401 - registration
from src.domains.agentverse.agents.roles.thoth.spec import role_spec
from src.domains.agentverse.dependencies.genesis_local import THOTH_PROVISION
from src.domains.agentverse.registries import role_registry_instance


def test_thoth_role_is_registered_and_conversational() -> None:
    role = role_registry_instance.get_role("thoth")
    assert role is role_spec
    assert (role.agent_group, role.agent_subclass) == ("interactive_ui", "conversational")
    assert supports_v2_conversation("chat", "thoth")


def test_thoth_role_requires_no_tools_and_names_no_handlers() -> None:
    assert role_spec.capabilities == {"required_tools": {}}
    assert role_spec.default_tools == [] and role_spec.handlers == {}
    text = json.dumps(role_spec.to_metadata(), default=str).lower()
    assert "tools-first" not in text and "system_tools" not in text


def test_role_rules_serialize_into_the_registry_manifest() -> None:
    manifest = role_spec.to_metadata()
    json.dumps(manifest, default=str)
    assert [rule["id"] for rule in manifest["rules"]] == [
        "thoth.interpret",
        "thoth.analyse",
        "thoth.evidence",
        "thoth.uncertainty",
        "thoth.inquiry",
        "thoth.explain",
        "thoth.sources",
        "thoth.scope",
    ]
    info = role_registry_instance.get_info("thoth")
    assert info is not None and info.metadata["rules"] == manifest["rules"]


def test_thoth_role_states_interpretation_without_action() -> None:
    rules = " ".join(rule.description for rule in role_spec.rules or [])
    for phrase in (
        "Interpret the knowledge",  # knowledge interpretation
        "analytical conversation",  # analytical conversation
        "Reason from evidence",  # evidence-aware reasoning
        "as research",  # research-oriented inquiry
        "State uncertainty explicitly",  # explicit uncertainty
        "Explain clearly",  # clear explanations
        "Interpretation is not action",
    ):
        assert phrase in rules, phrase
    assert "without claiming to have consulted them" in rules


def test_genesis_pins_thoth_to_its_dedicated_role() -> None:
    assert (THOTH_PROVISION.role_name, THOTH_PROVISION.personality_key) == ("thoth", "thoth")
