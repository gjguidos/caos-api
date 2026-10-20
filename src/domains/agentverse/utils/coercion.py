from __future__ import annotations

import json
import re
from enum import Enum
from typing import Any, TypeVar

from src.domains.agentverse.utils.strip_quotes import strip_quotes

E = TypeVar("E", bound=Enum)


def as_clean_str(val: Any) -> str:
    """
    Normalize arbitrary input into a clean, safe string.

    Purpose:
    - Prevent boolean "flag presence leaks" from becoming text.
    - Collapse list-like inputs into a single space-separated string.
    - Strip wrapping quotes often introduced by CLI or FE serialization.

    Rules:
    - None / bool          -> ""
    - list / tuple         -> "item1 item2 ..."
    - everything else      -> stripped string

    This is the *canonical* string normalizer used before any semantic parsing.
    """
    if val is None:
        return ""
    if isinstance(val, bool):
        return ""
    if isinstance(val, (list, tuple)):
        return " ".join(str(x).strip() for x in val if str(x).strip()).strip()
    s = str(val).strip()
    if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
        s = s[1:-1].strip()
    return s


def coerce_val(raw: str | None, spec: dict):
    t = (spec.get("type") or "string").lower()
    if raw is None:
        # flags like --enable (boolean true), or --no-enable (handled earlier)
        return True if t == "boolean" else None
    v = strip_quotes(raw)
    # allow JSON blobs for object/array if user passes --params='{"a":1}'
    if t in {"object", "array"}:
        try:
            parsed = json.loads(v)
            return parsed
        except Exception:
            if t == "array":
                return [p.strip() for p in v.split(",") if p.strip()]
            return {}
    if t in {"integer"}:
        return int(v)
    if t in {"number"}:
        return float(v)
    if t in {"boolean"}:
        return v.lower() not in {"0", "false", "no", "off"}
    # string + enum guard
    enum = spec.get("enum")
    if enum:
        lowmap = {str(x).lower(): x for x in enum}
        return lowmap.get(v.lower(), v)
    return v


def coerce_int(val: Any, default: int | None) -> int | None:
    """
    Coerce a value into an integer, never raising.

    Behavior:
    - None / "" / bool -> default
    - invalid values  -> default
    - valid int-like  -> int(val)

    Used for CLI / FE numeric fields where type safety is best-effort.
    """
    try:
        if val is None or val == "":
            return default
        if isinstance(val, bool):
            return default
        return int(val)
    except (TypeError, ValueError):
        return default


def coerce_bool(val: Any, default: bool | None = None) -> bool | None:
    """
    Coerce common CLI-style values into a boolean.

    Accepted truthy values:
      - True, "true", "yes", "y", "1", "on"

    Accepted falsy values:
      - False, "false", "no", "n", "0", "off"

    Anything else returns the provided default.

    This intentionally tolerates messy CLI / FE input.
    """
    if isinstance(val, bool):
        return val
    if val is None:
        return default

    s = str(val).strip().lower()
    if s in {"1", "true", "yes", "y", "on"}:
        return True
    if s in {"0", "false", "no", "n", "off"}:
        return False
    return default


def coerce_str_list(val: Any) -> list[str]:
    """
    Normalize list-like inputs into a list[str].

    Accepted inputs:
    - None / bool           -> []
    - "a,b,c"               -> ["a", "b", "c"]
    - list / tuple / set    -> [cleaned strings]
    - anything else         -> [as_clean_str(val)] or []

    This is used for multi-select UI fields and comma-separated CLI flags.
    """
    if val is None or isinstance(val, bool):
        return []
    if isinstance(val, str):
        parts = [p.strip() for p in val.split(",")]
        return [p for p in parts if p]
    if isinstance(val, (list, tuple, set)):
        out: list[str] = []
        for x in val:
            s = as_clean_str(x)
            if s:
                out.append(s)
        return out
    s = as_clean_str(val)
    return [s] if s else []


def coerce_enum(
    val: Any,
    enum_cls: type[E],
    *,
    default: E | None = None,
    case_insensitive: bool = True,
    allow_name: bool = True,
    allow_value: bool = True,
) -> E | None:
    """
    Coerce an arbitrary value into an Enum member.

    Supported inputs:
    - Enum member passthrough
    - Enum value (e.g. "clear" -> ToneOfVoice.CLEAR)
    - Enum name (e.g. "CLEAR" -> ToneOfVoice.CLEAR)
    - Case-insensitive matching (default)

    Safety guarantees:
    - Never raises
    - Ignores boolean flag leaks
    - Returns `default` on no match

    Designed for:
    - FE dropdowns
    - CLI flags
    - JSON payloads
    """
    if val is None:
        return default

    # Ignore boolean flag presence
    if isinstance(val, bool):
        return default

    # Passthrough
    if isinstance(val, enum_cls):
        return val

    # Direct value coercion
    if allow_value:
        try:
            return enum_cls(val)  # type: ignore[arg-type]
        except Exception:
            pass

    # String-based matching
    if isinstance(val, str):
        s = val.strip()
        if not s:
            return default

        key = s.lower() if case_insensitive else s

        if allow_name:
            for member in enum_cls:
                name = member.name.lower() if case_insensitive else member.name
                if key == name:
                    return member

        if allow_value:
            for member in enum_cls:
                if isinstance(member.value, str):
                    mval = member.value.lower() if case_insensitive else member.value
                    if key == mval:
                        return member

    # Last-resort stringification
    if allow_value:
        try:
            sval = str(val).strip()
        except Exception:
            return default

        if sval:
            key = sval.lower() if case_insensitive else sval
            for member in enum_cls:
                if str(member.value).strip() == sval:
                    return member
                if isinstance(member.value, str):
                    mval = member.value.lower() if case_insensitive else member.value
                    if key == mval:
                        return member

    return default


def coerce_json(s: str) -> dict[str, Any]:
    if not isinstance(s, str):
        return {}

    s = s.strip()

    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.I | re.S)

    m = re.search(r"\{.*\}", s, re.S)
    return json.loads(m.group(0) if m else s or "{}")
