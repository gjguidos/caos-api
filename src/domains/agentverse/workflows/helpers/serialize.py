from dataclasses import asdict, is_dataclass
from typing import Any

from src.domains.agentverse.workflows.spec import WorkflowSpec


def _serialize_workflow(spec: WorkflowSpec) -> dict[str, Any]:
    if hasattr(spec, "model_dump"):
        base = spec.model_dump()
    elif is_dataclass(spec):
        base = asdict(spec)
    elif hasattr(spec, "__dict__"):
        base = dict(spec.__dict__)
    else:
        base = {}

    # Add a small derived preview
    definition = base.get("definition") or {}
    steps = (definition.get("steps") or []) if isinstance(definition, dict) else []
    base["step_count"] = len(steps)
    base["has_pause"] = any((s.get("type") == "pause") for s in steps if isinstance(s, dict))

    # Ensure core fields
    base.setdefault("key", getattr(spec, "key", None))
    base.setdefault("name", getattr(spec, "name", None))
    base.setdefault("version", getattr(spec, "version", None))
    base.setdefault("tags", getattr(spec, "tags", []) or [])
    base.setdefault("inputs", getattr(spec, "inputs", []) or [])

    return base
