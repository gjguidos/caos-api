from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CONTEXT_KEY = "manager_facts"


@dataclass(frozen=True)
class ManagerFacts:
    """What the agent behind a ToolManager may actually run.

    A tool that drives other tools - or resolves a workflow by key - only sees an
    opaque runner, so the execution context is its only channel for asking. These
    are static for the life of a manager (``_factories`` is populated once at
    construction, ``agent.workflows`` is immutable), so they are built once and the
    same object rides every execution.

    Carried as one typed value rather than loose context keys so the gates that
    consume it fail loudly on absence instead of reading a missing key as "no
    restrictions" - see ``authorize_workflow_key``.
    """

    runnable_tool_keys: frozenset[str] = field(default_factory=frozenset)
    declared_workflow_keys: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_manager(cls, manager: Any) -> ManagerFacts:
        return cls(
            runnable_tool_keys=frozenset(manager.all_tool_keys()),
            declared_workflow_keys=frozenset(getattr(manager.agent, "workflows", None) or ()),
        )


def facts_from_context(context: dict | None) -> ManagerFacts:
    """The ``ManagerFacts`` on ``context``, or empty facts.

    Empty means "this agent declares nothing and can run nothing", which is the
    fail-closed reading every consumer wants. Manager-routed execution always sets
    the real value, so empty facts only arise off that path.
    """
    value = (context or {}).get(CONTEXT_KEY)
    return value if isinstance(value, ManagerFacts) else ManagerFacts()
