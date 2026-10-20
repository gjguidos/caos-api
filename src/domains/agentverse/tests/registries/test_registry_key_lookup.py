"""Tests for canonical-key addressing across the registries.

Every registry entry is identified by a unique, canonical ``key``. The registry
API and the frontend address entries by that key (never by display name, which
is allowed to duplicate). These tests lock in:

- ``RegistryItem`` always carries a ``key``.
- ``get_info`` / ``RegistryService.get_info`` resolve by key.
- ``list(include_metadata=True)`` exposes ``key``.
- Key registration is required, canonical, and unique.
"""

from __future__ import annotations

import pytest

from src.domains.agentverse.agents.agent_interface import AgentInterface
from src.domains.agentverse.agents.roles.role_spec import RolePolicy, RoleSpec
from src.domains.agentverse.agents.specs.agent_spec import (
    AgentGroup,
    AgentSpec,
    AgentSubclass,
    PromptProfile,
)
from src.domains.agentverse.exceptions import RegistrationError
from src.domains.agentverse.registries.agent_registry import AgentRegistry
from src.domains.agentverse.registries.base import Registry, RegistryItem
from src.domains.agentverse.registries.role_registry import RoleRegistry
from src.domains.agentverse.services.registry_service import RegistryService


class _Widget:
    """Trivial component for exercising the base registry."""

    __doc__ = "A widget."


class _DummyAgent(AgentInterface):
    """Minimal agent class for exercising registration."""


def _base_registry() -> Registry:
    reg: Registry = Registry(name="widget")
    reg.register(name="Alpha Widget", key="alpha", component=_Widget)
    return reg


def _agent_spec(key: str, name: str) -> AgentSpec:
    # Minimal valid spec, so these tests isolate key validation.
    return AgentSpec(
        key=key,
        name=name,
        description="d",
        version="1.0.0",
        agent_group=AgentGroup.COGNITIVE_CORE,
        agent_subclass=AgentSubclass.REASONING,
        prompt=PromptProfile(system="x"),
    )


class TestBaseRegistryKey:
    def test_registry_item_requires_key(self):
        with pytest.raises(Exception):
            RegistryItem(name="no key")  # type: ignore[call-arg]

    def test_get_info_resolves(self):
        reg = _base_registry()
        item = reg.get_info("alpha")
        assert item is not None
        assert item.key == "alpha"
        assert item.name == "Alpha Widget"

    def test_get_info_is_canonicalizing(self):
        reg = _base_registry()
        # Mixed case / whitespace resolves to the same canonical key.
        assert reg.get_info("  ALPHA  ") is not None

    def test_get_info_miss_returns_none(self):
        assert _base_registry().get_info("nope") is None

    def test_key_defaults_to_canonicalized_name(self):
        reg: Registry = Registry(name="widget")
        reg.register(name="Beta Widget", component=_Widget)
        # canonicalize() lowercases and turns whitespace runs into underscores,
        # so a display name and the key it addresses agree.
        assert reg.get_info("beta_widget") is not None
        assert reg.get_info("Beta Widget") is not None

    def test_same_class_reregistration_refreshes_metadata(self):
        reg: Registry = Registry(name="widget")
        reg.register(name="Beta", component=_Widget, metadata={"v": 1})
        reg.register(name="Beta", component=_Widget, metadata={"v": 2})
        assert reg.get_info("beta").metadata == {"v": 2}

    def test_last_registration_under_a_key_wins(self):
        """Registration is last-write-wins. The auto-router loader re-executes modules
        and ``pythonpath = [".", "src"]`` makes one source module importable under two
        dotted paths, so re-registration is the normal case, not a conflict.
        """
        reg: Registry = Registry(name="widget")

        class _Other:
            __doc__ = "other"

        reg.register(name="Beta", component=_Widget)
        reg.register(name="Beta", component=_Other)
        assert reg.get("beta") is _Other
        assert len(reg) == 1

    def test_list_exposes_key(self):
        entries = _base_registry().list(include_metadata=True)
        assert entries and all("key" in e for e in entries)
        assert entries[0]["key"] == "alpha"


class TestAgentRegistryKey:
    def test_registers_and_resolves_by_key(self):
        reg = AgentRegistry()
        reg.bind_spec(_agent_spec("chat", "Chat"))(_DummyAgent)
        assert reg.get_info("chat").name == "Chat"
        assert reg.get("chat") is _DummyAgent

    def test_missing_key_rejected(self):
        reg = AgentRegistry()
        with pytest.raises(RegistrationError):
            reg.bind_spec(_agent_spec("", "X"))

    def test_non_canonical_key_rejected(self):
        reg = AgentRegistry()
        with pytest.raises(RegistrationError):
            reg.bind_spec(_agent_spec("Bad Key", "X"))

    def test_last_binding_under_a_key_wins(self):
        reg = AgentRegistry()
        reg.bind_spec(_agent_spec("chat", "Chat"))(_DummyAgent)

        class _OtherAgent(AgentInterface):
            """Another agent registered under the same key."""

        reg.bind_spec(_agent_spec("chat", "Another Agent"))(_OtherAgent)
        assert reg.get("chat") is _OtherAgent
        assert reg.get_info("chat").name == "Another Agent"

    def test_spec_round_trips_through_metadata(self):
        reg = AgentRegistry()
        reg.bind_spec(_agent_spec("chat", "Chat"))(_DummyAgent)
        meta = reg.get_info("chat").metadata
        assert AgentSpec(**meta).model_dump(mode="json") == meta

    def test_non_agent_component_rejected(self):
        reg = AgentRegistry()
        with pytest.raises(RegistrationError):
            reg.bind_spec(_agent_spec("chat", "Chat"))(_Widget)


class TestRoleRegistryKey:
    """Roles are addressed by canonical key like every other registry. The registry
    stores the ``RoleSpec`` itself - there is no side map and no placeholder class.
    """

    @staticmethod
    def _spec(key: str = "general", name: str = "General") -> RoleSpec:
        return RoleSpec(key=key, name=name, policy=RolePolicy(name=key))

    def test_spec_is_the_registry_entry(self):
        reg = RoleRegistry()
        spec = self._spec()
        reg.bind_spec(spec)(_Widget)
        assert reg.get_role("general").key == "general"
        assert reg.get("general") is spec
        assert reg.get_info("general").name == "General"

    def test_equivalent_spec_rebinds_without_conflict(self):
        """Module re-execution by the auto-router loader rebuilds the spec, so an
        equivalent spec must refresh rather than collide."""
        reg = RoleRegistry()
        reg.bind_spec(self._spec())(_Widget)
        reg.bind_spec(self._spec())(_Widget)  # must not raise
        assert len(reg) == 1

    def test_rebinding_swaps_in_the_new_spec_object(self):
        """Re-registering must store the new spec object, or a module re-executed by
        the loader keeps its stale handler callables bound.
        """
        reg = RoleRegistry()

        async def h_old(ctx, spec):
            return ctx

        async def h_new(ctx, spec):
            return ctx

        first = self._spec()
        first.handlers = {"task": h_old}
        second = self._spec()
        second.handlers = {"task": h_new}

        reg.bind_spec(first)(_Widget)
        reg.bind_spec(second)(_Widget)

        assert reg.get("general") is second
        assert reg.get_role("general").handlers["task"] is h_new

    def test_non_canonical_key_rejected_at_spec_construction(self):
        with pytest.raises(RegistrationError):
            RoleSpec(key="Bad Key", name="Bad", policy=RolePolicy(name="bad"))

    def test_display_name_addresses_the_same_entry(self):
        reg = RoleRegistry()
        reg.bind_spec(self._spec(key="compliance_auditor", name="Compliance Auditor"))(_Widget)
        assert reg.get_role("Compliance Auditor").key == "compliance_auditor"

    def test_unknown_role_raises_lookup_error(self):
        with pytest.raises(LookupError):
            RoleRegistry().get_role("nope")

    def test_unregister_drops_the_spec_too(self):
        reg = RoleRegistry()
        reg.bind_spec(self._spec())(_Widget)
        reg.unregister("general")
        assert "general" not in reg
        with pytest.raises(LookupError):
            reg.get_role("general")

    def test_newer_spec_under_taken_key_replaces_it(self):
        reg = RoleRegistry()
        reg.bind_spec(self._spec())(_Widget)
        newer = RoleSpec(
            key="general", name="General", version="2.0.0", policy=RolePolicy(name="general")
        )
        reg.bind_spec(newer)(_Widget)
        assert reg.get("general") is newer
        assert reg.get_info("general").version == "2.0.0"


class TestRegistryServiceGetInfo:
    def test_get_info_resolves_by_key(self):
        svc = RegistryService(registries={"widget": _base_registry()})
        item = svc.get_info("widget", "alpha")
        assert item is not None and item.key == "alpha"

    def test_get_info_unknown_key_raises(self):
        svc = RegistryService(registries={"widget": _base_registry()})
        with pytest.raises(KeyError):
            svc.get_info("widget", "does-not-exist")
