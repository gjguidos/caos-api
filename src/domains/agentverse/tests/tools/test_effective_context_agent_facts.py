"""``ManagerFacts`` are on the context of every routed execution.

The facts are consumed by gates that fail closed, so they must be set
unconditionally rather than as a side effect of some other branch - absence
would otherwise silently read as "no restrictions".
"""

from types import SimpleNamespace

from src.domains.agentverse.registries.registries import tool_registry_instance
from src.domains.agentverse.tools.base import BaseTool
from src.domains.agentverse.tools.spec import ToolSpec
from src.domains.agentverse.tools.tool_manager import ToolManager
from src.domains.agentverse.tools.tool_manager_v1.execution.context import (
    build_effective_context,
)
from src.domains.agentverse.tools.tool_manager_v1.facts import CONTEXT_KEY, ManagerFacts


class _EquippedTool(BaseTool):
    name = "AgentVerse Ctx Equipped Tool"
    key = "agentverse_ctx_equipped"
    version = "1.0.0"
    parameters = {}


class _WorkflowOnlyTool(BaseTool):
    name = "AgentVerse Ctx Workflow Only Tool"
    key = "agentverse_ctx_workflow_only"
    version = "1.0.0"
    parameters = {}


def _agent(workflows=None) -> SimpleNamespace:
    return SimpleNamespace(
        id="agent-1",
        name="test-agent",
        type="test",
        metadata={},
        capabilities=set(),
        rule_pack_ids=[],
        workflows=workflows if workflows is not None else [],
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


def _manager(agent) -> ToolManager:
    return ToolManager(
        agent,
        [
            ToolSpec(key=_EquippedTool.key, name=_EquippedTool.name),
            ToolSpec(key=_WorkflowOnlyTool.key, name=_WorkflowOnlyTool.name, scope="workflow"),
        ],
    )


def _build(agent, kwargs=None) -> dict:
    for cls in (_EquippedTool, _WorkflowOnlyTool):
        tool_registry_instance.register_tool(key=cls.key, name=cls.name, component=cls)
    try:
        _text, effective_ctx, _base = build_effective_context(
            _manager(agent),
            text="hello",
            history=None,
            file=None,
            extra=None,
            kwargs=kwargs if kwargs is not None else {},
        )
        return effective_ctx
    finally:
        for cls in (_EquippedTool, _WorkflowOnlyTool):
            tool_registry_instance.unregister(cls.key)


class TestRunnableToolKeys:
    def test_includes_workflow_scoped_tools(self) -> None:
        """The workflow engine may run them, so the pre-check must count them as
        runnable even though they are off the direct-invocation surface."""
        facts = _build(_agent())[CONTEXT_KEY]

        assert facts.runnable_tool_keys == {_EquippedTool.key, _WorkflowOnlyTool.key}


class TestDeclaredWorkflowKeys:
    def test_reflects_the_agents_attached_workflows(self) -> None:
        facts = _build(_agent(workflows=["wf_a", "wf_b"]))[CONTEXT_KEY]

        assert facts.declared_workflow_keys == {"wf_a", "wf_b"}

    def test_agent_with_no_workflows_declares_nothing(self) -> None:
        facts = _build(_agent(workflows=[]))[CONTEXT_KEY]

        assert facts.declared_workflow_keys == frozenset()


class TestCallerSuppliedContext:
    def test_inherited_facts_are_overwritten_by_this_manager(self) -> None:
        """A nested call inherits the parent's context dict. The facts describe the
        manager executing, so a stale inherited value must not survive and widen
        what the child is allowed to reach."""
        inherited = {
            CONTEXT_KEY: ManagerFacts(
                runnable_tool_keys=frozenset({"anything"}),
                declared_workflow_keys=frozenset({"wf_stale"}),
            )
        }

        facts = _build(_agent(workflows=["wf_a"]), kwargs={"context": inherited})[CONTEXT_KEY]

        assert facts.declared_workflow_keys == {"wf_a"}
        assert facts.runnable_tool_keys == {_EquippedTool.key, _WorkflowOnlyTool.key}
