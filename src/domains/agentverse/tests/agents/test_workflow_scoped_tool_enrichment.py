"""Workflow-required tools are auto-equipped at workflow scope (D2/D3).

Attaching a workflow must never widen the chat surface, and must never quietly
override tuning the agent already declared for a tool it equips itself.

Resolving *which* tools a workflow requires is WorkflowService's job and is
covered in ``tests/services/test_workflow_service.py``.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.domains.agentverse.agents.factory import AgentFactory
from src.domains.agentverse.tools.spec import AgentToolRef


def _role_spec(capabilities: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(name="general", default_tools=[], capabilities=capabilities or {})


def _factory() -> AgentFactory:
    return AgentFactory()


class TestEnrichmentScoping:
    def test_workflow_only_tool_is_scoped_to_workflow(self) -> None:
        tools = _factory()._enrich_agent_tools([], _role_spec(), workflow_tools={"writer"})

        writer = next(t for t in tools if t.key == "writer")
        assert writer.scope == "workflow"

    def test_equipped_tool_keeps_agent_scope_and_its_config(self) -> None:
        """D3: equipping a tool is how you tune it. A workflow also requiring it
        must not demote it to workflow scope or drop the agent's config."""
        tools = _factory()._enrich_agent_tools(
            [AgentToolRef(key="writer", config={"tone": "dry"})],
            _role_spec(),
            workflow_tools={"writer"},
        )

        writer = next(t for t in tools if t.key == "writer")
        assert writer.scope == "agent"
        assert writer.config["tone"] == "dry"

    def test_core_tools_stay_agent_scoped(self) -> None:
        tools = _factory()._enrich_agent_tools([], _role_spec(), workflow_tools={"writer"})

        assert {t.key for t in tools if t.scope == "agent"} >= {"chat", "datetime", "cta"}

    def test_workflow_tools_are_additive(self) -> None:
        tools = _factory()._enrich_agent_tools([], _role_spec(), workflow_tools={"writer"})

        assert "writer" in {t.key for t in tools}

    def test_role_system_tools_carry_the_typed_system_scope(self) -> None:
        """Scope is one field, not a typed one for workflow tools and a
        ``config["_scope"]`` string for system ones."""
        role_spec = _role_spec({"system_tools": {"drafting": "writer"}})

        tools = _factory()._enrich_agent_tools([], role_spec)

        writer = next(t for t in tools if t.key == "writer")
        assert writer.scope == "system"
        assert "_scope" not in writer.config
