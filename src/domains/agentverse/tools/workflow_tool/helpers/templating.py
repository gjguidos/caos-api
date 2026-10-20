from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

_VAR_RE = re.compile(r"\$\{([^}]+)\}")


class MissingTemplateValueError(ValueError):
    pass


def deep_get(d: dict[str, Any], path: str, default: Any = None) -> Any:
    cur: Any = d

    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return default

    return cur


def deep_set(d: dict[str, Any], path: str, value: Any) -> None:
    cur = d
    parts = path.split(".")

    for part in parts[:-1]:
        if not isinstance(cur.get(part), dict):
            cur[part] = {}
        cur = cur[part]

    cur[parts[-1]] = value


def render_template(
    value: Any,
    ctx: dict[str, Any],
    *,
    strict: bool = True,
    encode: Callable[[Any], str] = str,
) -> Any:
    """Substitute ``${...}`` references in ``value`` against ``ctx``.

    ``encode`` turns a resolved value into its substituted text. It defaults to
    ``str`` (the historical behavior); callers that feed a tool command channel
    pass a JSON-aware encoder so structured (``object``/``list``) bindings arrive
    as real JSON rather than a Python repr. Literal ``|default`` values are never
    passed through ``encode`` — they are authored text and substituted verbatim.
    """
    if isinstance(value, dict):
        return {k: render_template(v, ctx, strict=strict, encode=encode) for k, v in value.items()}

    if isinstance(value, list):
        return [render_template(v, ctx, strict=strict, encode=encode) for v in value]

    if not isinstance(value, str):
        return value

    def replace(match: re.Match) -> str:
        expr = match.group(1).strip()
        key, default = _split_default(expr)

        resolved = deep_get(ctx, key)

        if resolved is None:
            if default is not None:
                return default
            if strict:
                raise MissingTemplateValueError(f"Missing template value: {key}")
            return ""

        return encode(resolved)

    return _VAR_RE.sub(replace, value)


def parse_single_reference(value: str) -> str:
    """Return the lookup key of a value that must be exactly one ``${...}``
    reference (e.g. ``"${outputs.p.image}"`` -> ``"outputs.p.image"``).

    ``|default`` fallbacks are not supported - the whole expression is treated
    as the lookup key. Raises ``MissingTemplateValueError`` when the value is
    not a single reference.
    """
    match = _VAR_RE.fullmatch(value.strip())
    if match is None:
        raise MissingTemplateValueError(f"expected exactly one ${{...}} reference, got: {value!r}")

    return match.group(1).strip()


def resolve_single_reference(value: str, ctx: dict[str, Any]) -> Any:
    """Resolve a value that must be exactly one ``${...}`` reference, returning
    the RAW resolved object (not string-substituted).

    Raises ``MissingTemplateValueError`` when the value is not a single
    reference or the reference does not resolve.
    """
    key = parse_single_reference(value)
    resolved = deep_get(ctx, key)
    if resolved is None:
        raise MissingTemplateValueError(f"Missing template value: {key}")

    return resolved


def _split_default(expr: str) -> tuple[str, str | None]:
    if "|" not in expr:
        return expr.strip(), None

    key, default = expr.split("|", 1)
    return key.strip(), default.strip()
