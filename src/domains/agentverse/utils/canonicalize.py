"""
String canonicalization utilities.

Provides various string normalization functions for consistent key/identifier handling
across the agentverse domain.
"""

from __future__ import annotations

from src.domains.agentverse.exceptions import RegistrationError


def require_canonical_key(key: str | None, *, kind: str, name: str) -> str:
    """Validate an author-provided, canonical key. Returns it unchanged.

    Called from spec construction (``AgentSpec``, ``RoleSpec``) so a typo surfaces
    where the spec is written rather than silently coerced at registration time.
    """
    if not key:
        raise RegistrationError(message=f"{kind} '{name}' requires a canonical `key`")
    if key != canonicalize(key):
        raise RegistrationError(
            message=f"{kind} key '{key}' must be canonical (expected '{canonicalize(key)}')"
        )
    return key


def canonicalize(s: str | None, *, allow_special: bool = True) -> str:
    """
    Canonicalize a string for use as a key or identifier.

    Normalizes by:
    - Stripping surrounding whitespace
    - Converting to lowercase
    - Collapsing internal whitespace runs into a single underscore
    - Removing non-alphanumeric characters (optionally keeping _ and -)

    This is the single key function for every registry: a display name and the
    canonical key it addresses must agree under it ("Datetime Tool" -> "datetime_tool").

    Args:
        s: String to canonicalize (None returns empty string)
        allow_special: If True, preserves underscore and hyphen characters.
                      If False, removes all non-alphanumeric characters.

    Returns:
        Canonicalized string

    Examples:
        >>> canonicalize("DateTime Tool")
        'datetime_tool'
        >>> canonicalize("DateTime Tool", allow_special=False)
        'datetimetool'
        >>> canonicalize("  My-Tool_Name  ")
        'my-tool_name'
    """
    if not allow_special:
        # strict alphanumeric only
        return "".join(ch for ch in (s or "").strip().lower() if ch.isalnum())

    # whitespace runs become a single underscore, so display names and the keys
    # they address agree ("Datetime  Tool" -> "datetime_tool")
    s = "_".join((s or "").split()).lower()
    return "".join(ch for ch in s if ch.isalnum() or ch in ("_", "-"))


def canonicalize_simple(s: str | None) -> str:
    """
    Simple canonicalization - strips and lowercases only.

    Preserves all characters except leading/trailing whitespace.
    Used in contexts where special characters should be preserved.

    Args:
        s: String to canonicalize (None returns empty string)

    Returns:
        Stripped and lowercased string

    Examples:
        >>> canonicalize_simple("  DateTime Tool  ")
        'datetime tool'
    """
    return (s or "").strip().lower()
