"""Explicit AOSA reference formats shared by read policy and administrative audit.

No ownership is derived here. Schema defaults and free-form prose are not
resource declarations; unsupported or dynamic workflow addresses fail closed.
"""

import shlex
from collections.abc import Iterator
from typing import Any

from src.domains.agentverse.tools.workflow_tool.libs.engine.resolve_tool_key import (
    resolve_tool_key_from_command,
)

SCHEMA_FIELDS = frozenset(
    {"parameters_schema", "config_schema", "input_jsonschema", "output_jsonschema"}
)


def direct_references(metadata: dict[str, Any]) -> Iterator[tuple[str, Any]]:
    """Yield only addresses explicitly carried in supported reference fields.

    A None/malformed address is deliberately yielded so authorization denies it.
    Role required/optional tool maps carry keys->labels; system tool and required
    workflow maps carry aliases->keys. These directions are different contracts.
    """
    lists = {
        "tools": "tool",
        "default_tools": "tool",
        "required_tools": "tool",
        "rule_pack_ids": "rulepack",
        "rule_packs": "rulepack",
        "required_rulepacks_hint": "rulepack",
        "uses_workflows": "workflow",
        "workflows": "workflow",
    }
    singles = {
        "personality_key": "personality",
        "role_name": "role",
        "tool_key": "tool",
        "workflow_key": "workflow",
    }
    for field, kind in lists.items():
        value = metadata.get(field)
        if isinstance(value, list):
            for entry in value:
                yield kind, entry
        elif field == "required_tools" and isinstance(value, dict):
            for key in value:
                yield "tool", key
        elif field in metadata and value is not None:
            yield kind, None
    for field, kind in singles.items():
        if metadata.get(field):
            yield kind, metadata[field]
    for field in ("system_tools", "required_workflows"):
        value = metadata.get(field)
        if isinstance(value, dict):
            for key in value.values():
                yield "workflow" if field == "required_workflows" else "tool", key
        elif field in metadata and value is not None:
            yield "workflow" if field == "required_workflows" else "tool", None
    if isinstance(metadata.get("optional_tools"), dict):
        for key in metadata["optional_tools"]:
            yield "tool", key
    if metadata.get("type") in ("tool", "workflow") and "key" in metadata:
        yield metadata["type"], metadata["key"]
    command = metadata.get("command")
    if isinstance(command, str) and (
        command.lstrip().startswith("@") or metadata.get("type") == "tool"
    ):
        key = resolve_tool_key_from_command(command)
        yield "tool", key
        if key == "workflow":
            try:
                tokens = shlex.split(command)
            except ValueError:
                yield "workflow", None
                return
            targets: list[str | None] = []
            for index, token in enumerate(tokens):
                if token.startswith("--workflow_key="):
                    targets.append(token.split("=", 1)[1])
                elif token == "--workflow_key":
                    targets.append(tokens[index + 1] if index + 1 < len(tokens) else None)
            # Runtime-dependent targets cannot establish a readable reference.
            yield "workflow", targets[0] if len(targets) == 1 else None


def reference_key(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return value.get("key") or value.get("name")
    return None


def inventory_references(metadata: dict[str, Any], *, depth: int = 0) -> list[dict[str, Any]]:
    """Content-free dependency inventory, without resolving or assigning owners."""
    if depth > 32:
        return [{"kind": "unknown", "key": None, "reason": "depth_limit"}]
    refs = [
        {"kind": kind, "key": reference_key(value)} for kind, value in direct_references(metadata)
    ]
    for field, value in metadata.items():
        if field in SCHEMA_FIELDS:
            continue
        entries = value if isinstance(value, list) else [value]
        for entry in entries:
            if isinstance(entry, dict):
                refs.extend(inventory_references(entry, depth=depth + 1))
    return refs


def nested_nodes(value: Any, *, depth: int = 0) -> Iterator[dict[str, Any]]:
    """Immediate dict children, including dictionaries inside nested arrays.

    Sentinel policy denies excessively deep arrays without unbounded recursion.
    Dict recursion remains the caller's responsibility and carries its budget.
    """
    if depth > 32:
        yield {"exposure": "invalid"}
    elif isinstance(value, dict):
        yield value
    elif isinstance(value, list):
        for entry in value:
            yield from nested_nodes(entry, depth=depth + 1)
