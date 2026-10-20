def collect_param_specs(tool_cls) -> dict:
    # Prefer class.parameters; fall back to registry schema if you want
    return getattr(tool_cls, "parameters", {}) or {}


def resolve_param_name(key_norm: str, specs: dict) -> str | None:
    """Map --flag key to a canonical parameter (supports aliases)."""
    # exact
    for name in specs:
        if key_norm == name.replace("-", "_").lower():
            return name
    # aliases
    for name, spec in specs.items():
        for a in spec.get("aliases") or []:
            if key_norm == a.replace("-", "_").lower():
                return name
    return None
