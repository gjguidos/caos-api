from typing import Any

from src.domains.agentverse.tools.tool_manager_v1.dep_map import dep_map as _dep_map
from src.domains.agentverse.tools.tool_manager_v1.normalize import normalize_key as _normalize_key
from src.domains.agentverse.tools.tool_manager_v1.tags import SYSTEM_TOOL_TAGS


def is_system_tool_meta(meta: dict, *, system_tags: set[str] = SYSTEM_TOOL_TAGS) -> bool:
    tags = set((meta or {}).get("tags") or [])
    return not system_tags.isdisjoint(tags)


def is_system_tool_factories(factories: dict, key: str) -> bool:
    f = factories.get(key) or {}
    return is_system_tool_meta(f.get("meta") or {})


def evaluate_invocation_scope(
    agent: Any, factories: dict, key: str, context: dict | None = None
) -> str | None:
    """Why ``key`` may not be invoked *from here*, or ``None`` when it may.

    Separate from :func:`evaluate_policy` because it answers a different question:
    scope is about the call site, not about the agent's fitness to run the tool at
    all. Only the execution router asks it - construction and introspection paths
    legitimately reach workflow-scoped tools.
    """
    f = factories.get(key) or {}
    if (f.get("scope") or "agent") != "workflow":
        return None
    if int((context or {}).get("workflow_depth", 0) or 0) >= 1:
        return None

    agent_label = getattr(agent, "name", None) or getattr(agent, "id", None) or "this agent"
    return (
        f"Tool '{key}' is equipped on agent '{agent_label}' only to satisfy an "
        f"attached workflow's requirements and cannot be invoked directly. "
        f"Add '{key}' to the agent's tools to invoke it."
    )


def evaluate_policy(
    agent: Any,
    factories: dict,
    key: str,
    context: dict | None = None,
) -> str | None:
    """Why ``key`` may not run for ``agent``, or ``None`` when it may.

    The reason travels back to the caller rather than being logged and dropped:
    a blocked nested tool call surfaces it verbatim, so a workflow author sees
    *which* gate closed instead of an empty result.
    """
    context = context or {}
    agent_label = getattr(agent, "name", None) or getattr(agent, "id", None) or "this agent"

    f = factories.get(key)
    if not f:
        equipped = ", ".join(sorted(factories)) or "none"
        return (
            f"Tool '{key}' is not equipped on agent '{agent_label}'. A workflow step can "
            f"only invoke tools configured on the agent running it - add '{key}' to the "
            f"agent's tools. Currently equipped: {equipped}."
        )

    normalized_key = _normalize_key(key)

    workflow_depth = int(context.get("workflow_depth", 0) or 0)
    allow_nested_workflows = context.get("allow_nested_workflows") is True
    is_nested_workflow_call = (
        normalized_key in {"flow", "workflow"} and workflow_depth >= 1 and allow_nested_workflows
    )

    if normalized_key in {"flow", "workflow"} and workflow_depth >= 1:
        if not allow_nested_workflows:
            return (
                f"Tool '{key}' is a workflow invoked at depth {workflow_depth} and nested "
                f"workflows are not enabled for this run."
            )

    cls = f["cls"]

    required = set(getattr(cls, "required_dependencies", [])) | set(
        getattr(cls, "required_deps", [])
    )
    have = {k for k, v in _dep_map(agent).items() if v is not None}
    if not required.issubset(have):
        return f"missing_dependencies required={sorted(required)} have={sorted(have)}"

    needed_caps = set(getattr(cls, "required_caps", []))
    have_caps = set(getattr(agent, "capabilities", set()))
    if not needed_caps.issubset(have_caps):
        return f"missing_caps required={sorted(needed_caps)} have={sorted(have_caps)}"

    allow = getattr(cls, "allowed_agent_types", None)
    deny = set(getattr(cls, "forbidden_agent_types", []))
    if allow and agent.type not in allow:
        return f"agent_type_not_allowed type={agent.type} allow={allow}"
    if agent.type in deny:
        return f"agent_type_denied type={agent.type} deny={sorted(deny)}"

    allow_tools = set((agent.metadata or {}).get("allow_tools", []))
    deny_tools = set((agent.metadata or {}).get("deny_tools", []))

    normalized_allow_tools = {_normalize_key(t) for t in allow_tools}
    normalized_deny_tools = {_normalize_key(t) for t in deny_tools}

    if normalized_key in normalized_deny_tools:
        return f"Tool '{key}' is on agent '{agent_label}' deny_tools list."

    # Important: allow nested workflow bridge even if agent allow_tools excludes flow.
    if allow_tools and normalized_key not in normalized_allow_tools:
        if not is_nested_workflow_call:
            return (
                f"Tool '{key}' is not on agent '{agent_label}' allow_tools list "
                f"({', '.join(sorted(allow_tools))})."
            )

    return None
