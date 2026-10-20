"""The runnable/invocable seam.

``_factories`` is the runnable set - what the workflow engine may construct and
``evaluate_policy`` may authorize. ``list_tool_keys()`` is the invocable set -
the direct ``@<tool_key>`` chat surface. Workflow-scoped tools are in the first
and not the second.
"""

from types import SimpleNamespace

from src.domains.agentverse.registries.registries import tool_registry_instance
from src.domains.agentverse.tools.base import BaseTool
from src.domains.agentverse.tools.spec import ToolSpec
from src.domains.agentverse.tools.tool_manager import ToolManager
from src.domains.agentverse.tools.tool_manager_v1.policy import (
    evaluate_invocation_scope,
    evaluate_policy,
)


class _EquippedTool(BaseTool):
    name = "AgentVerse Scope Equipped Tool"
    key = "agentverse_scope_equipped"
    version = "1.0.0"
    parameters = {}


class _WorkflowOnlyTool(BaseTool):
    name = "AgentVerse Scope Workflow Only Tool"
    key = "agentverse_scope_workflow_only"
    version = "1.0.0"
    parameters = {}


def _agent() -> SimpleNamespace:
    return SimpleNamespace(
        id="agent-1",
        name="test-agent",
        type="test",
        metadata={},
        capabilities=set(),
        llm=None,
        image=None,
        video=None,
        audio=None,
        db=None,
        cache=None,
        vectordb=None,
        messaging=None,
        http={},
    )


def _manager() -> ToolManager:
    return ToolManager(
        _agent(),
        [
            ToolSpec(key=_EquippedTool.key, name=_EquippedTool.name),
            ToolSpec(
                key=_WorkflowOnlyTool.key,
                name=_WorkflowOnlyTool.name,
                scope="workflow",
            ),
        ],
    )


def _register() -> None:
    for cls in (_EquippedTool, _WorkflowOnlyTool):
        tool_registry_instance.register_tool(key=cls.key, name=cls.name, component=cls)


def _unregister() -> None:
    for cls in (_EquippedTool, _WorkflowOnlyTool):
        tool_registry_instance.unregister(cls.key)


class TestToolSpecScope:
    def test_scope_defaults_to_agent(self) -> None:
        assert ToolSpec(key="x", name="X").scope == "agent"

    def test_scope_survives_config_coercion(self) -> None:
        """A real field, not ``config["_scope"]`` - so ``maybe_coerce_config``
        can never drop it while folding spec config into the tool's config."""
        spec = ToolSpec(key="x", name="X", scope="workflow", config={"a": 1})
        assert spec.scope == "workflow"


class TestInvocableVersusRunnable:
    def test_workflow_scoped_tool_is_runnable_but_not_invocable(self) -> None:
        _register()
        try:
            manager = _manager()

            assert manager.list_tool_keys() == [_EquippedTool.key]
            assert manager.ensure_instance(_WorkflowOnlyTool.key) is not None
        finally:
            _unregister()

    def test_full_inventory_available_on_request(self) -> None:
        _register()
        try:
            manager = _manager()

            assert sorted(manager.all_tool_keys()) == sorted(
                [_EquippedTool.key, _WorkflowOnlyTool.key]
            )
        finally:
            _unregister()

    def test_at_token_selection_cannot_reach_a_workflow_scoped_tool(self) -> None:
        from src.domains.agentverse.agents.orchestrator.built_in_phases.helpers.tool_helpers import (
            extract_selected_tool,
        )

        _register()
        try:
            manager = _manager()

            assert (
                extract_selected_tool(f"@{_EquippedTool.key} do it", manager) == _EquippedTool.key
            )
            assert extract_selected_tool(f"@{_WorkflowOnlyTool.key} do it", manager) is None
        finally:
            _unregister()


class TestInvocationScopeGate:
    def test_workflow_scoped_tool_rejected_outside_a_workflow(self) -> None:
        _register()
        try:
            manager = _manager()
            reason = evaluate_invocation_scope(
                manager.agent,
                manager._factories,
                _WorkflowOnlyTool.key,
                {"workflow_depth": 0},
            )

            assert reason is not None
            assert "cannot be invoked directly" in reason
        finally:
            _unregister()

    def test_workflow_scoped_tool_allowed_inside_a_workflow(self) -> None:
        _register()
        try:
            manager = _manager()
            reason = evaluate_invocation_scope(
                manager.agent,
                manager._factories,
                _WorkflowOnlyTool.key,
                {"workflow_depth": 1},
            )

            assert reason is None
        finally:
            _unregister()

    def test_agent_scoped_tool_unaffected_at_depth_zero(self) -> None:
        _register()
        try:
            manager = _manager()
            reason = evaluate_invocation_scope(
                manager.agent,
                manager._factories,
                _EquippedTool.key,
                {"workflow_depth": 0},
            )

            assert reason is None
        finally:
            _unregister()

    def test_scope_is_not_a_general_policy_concern(self) -> None:
        """evaluate_policy answers "may this agent use this tool at all". Scope is
        a call-site question, so construction and introspection stay unaffected."""
        _register()
        try:
            manager = _manager()

            assert (
                evaluate_policy(
                    manager.agent,
                    manager._factories,
                    _WorkflowOnlyTool.key,
                    {"workflow_depth": 0},
                )
                is None
            )
        finally:
            _unregister()
