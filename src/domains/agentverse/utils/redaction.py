"""Payload summarization and secret redaction for run reports.

Run reports are persisted and served over an API, so nothing that reaches them
may carry a credential, an unbounded payload, or a piece of runtime plumbing.
Three rules apply to every value, at every depth:

- Keys naming a credential are replaced with ``REDACTED``.
- Keys naming internal plumbing are dropped outright - the caller's own
  argument bags travel nested inside each other, so a one-level filter at the
  call site would not hold.
- URLs keep only scheme/host/path; the query string is dropped so a SAS
  ``?sig=...`` minted into a workflow tool arg never lands in Mongo.

Both key rules match on word boundaries rather than raw substrings: ``author``,
``keywords`` and ``monkey`` are ordinary report content, and a report whose
every other field reads ``REDACTED`` is one nobody opens twice.

The credential denylist fails open: an unrecognized key holding a secret is
recorded. That is an accepted trade-off while reports stay inside the owner's
scope.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

REDACTED = "***redacted***"

MAX_STRING = 200
MAX_ITEMS = 20
MAX_DEPTH = 4

# Matched against the key's words, not its raw text. A key is secret if any of
# its words is one of these.
_SECRET_KEY_WORDS = frozenset(
    {
        "token",
        "tokens",
        "secret",
        "secrets",
        "apikey",
        "password",
        "passwd",
        "pwd",
        "sas",
        "credential",
        "credentials",
        "authorization",
        "auth",
    }
)

# "key" is secret as a noun (``api_key``, ``key``) but not as a qualifier
# (``key_points``, ``key_findings``), so it only counts in trailing position.
_SECRET_KEY_SUFFIXES = frozenset({"key", "keys"})

# Runtime plumbing threaded through tool kwargs: emitters, the trace sink
# itself, the agent, the Starlette request, and the argument bags that carry
# copies of all of the above. None of it is a caller-supplied argument.
_INTERNAL_KEY_WORDS = frozenset(
    {
        "tool_kwargs",
        "overrides",
        "context",
        "history",
        "agent",
        "request",
    }
)

_WORD_SPLIT_RE = re.compile(r"[^a-z0-9]+")

_URL_RE = re.compile(r"^[a-z][a-z0-9+.\-]*://", re.IGNORECASE)

_BLOB_PREFIXES = ("data:", "b64_json")


def _key_words(key: str) -> list[str]:
    """Split a key into lowercase words: ``sasUrl`` and ``sas_url`` both yield ``sas``."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", key)
    return [word for word in _WORD_SPLIT_RE.split(spaced.lower()) if word]


def is_secret_key(key: Any) -> bool:
    """True when a mapping key names a credential.

    Word-based: ``api_key``, ``sasUrl`` and ``client-secret`` match;
    ``keywords``, ``key_points`` and ``author`` do not.
    """
    if not isinstance(key, str):
        return False
    words = _key_words(key)
    if not words:
        return False
    return bool(set(words) & _SECRET_KEY_WORDS) or words[-1] in _SECRET_KEY_SUFFIXES


def is_internal_key(key: Any) -> bool:
    """True when a mapping key names runtime plumbing rather than report content.

    Underscore-prefixed keys are internal by convention throughout the tool
    layer (``_socket_emitter``, ``_tool_trace``, ``_agent_id``).
    """
    if not isinstance(key, str):
        return False
    return key.startswith("_") or key.lower() in _INTERNAL_KEY_WORDS


def strip_url_query(value: str) -> str:
    """Drop the query string and fragment from a URL, keeping it identifiable."""
    if not _URL_RE.match(value):
        return value
    try:
        parts = urlsplit(value)
    except ValueError:
        return REDACTED
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _clean_string(value: str, limit: int = MAX_STRING) -> str:
    """Strip a URL's query string, then truncate. The one string rule."""
    stripped = strip_url_query(value)
    if len(stripped) <= limit:
        return stripped
    return stripped[:limit] + "…"


def summarize_value(value: Any, *, _depth: int = 0) -> Any:
    """Reduce an arbitrary value to something small, safe and JSON-friendly.

    Scalars pass through, strings are URL-stripped then truncated, large
    binary-ish payloads collapse to ``{"type": ..., "bytes": ...}``, and
    containers are summarized element-wise up to a depth and item cap.
    """
    if value is None or isinstance(value, bool | int | float):
        return value

    if isinstance(value, bytes | bytearray | memoryview):
        return {"type": "bytes", "bytes": len(bytes(value))}

    if isinstance(value, str):
        stripped = strip_url_query(value)
        if len(stripped) > MAX_STRING and _looks_like_blob(stripped):
            return {"type": "string", "bytes": len(stripped)}
        return _clean_string(stripped)

    if _depth >= MAX_DEPTH:
        return {"type": type(value).__name__}

    if isinstance(value, dict):
        reportable = [
            (key if isinstance(key, str) else str(key), item) for key, item in value.items()
        ]
        reportable = [(name, item) for name, item in reportable if not is_internal_key(name)]

        out: dict[str, Any] = {
            name: (REDACTED if is_secret_key(name) else summarize_value(item, _depth=_depth + 1))
            for name, item in reportable[:MAX_ITEMS]
        }
        if len(reportable) > MAX_ITEMS:
            out["…"] = f"+{len(reportable) - MAX_ITEMS} more"
        return out

    if isinstance(value, list | tuple | set):
        items = list(value)
        summarized = [summarize_value(item, _depth=_depth + 1) for item in items[:MAX_ITEMS]]
        if len(items) > MAX_ITEMS:
            summarized.append(f"+{len(items) - MAX_ITEMS} more")
        return summarized

    return _clean_string(str(value))


def _looks_like_blob(value: str) -> bool:
    head = value[:64].lower()
    if any(head.startswith(prefix) for prefix in _BLOB_PREFIXES):
        return True
    # Long unbroken base64-ish runs are payloads, not prose.
    return " " not in value[:256] and bool(re.fullmatch(r"[A-Za-z0-9+/=_\-]+", value[:256]))


def summarize_args(args: Any) -> dict[str, Any]:
    """Summarize a tool's caller-supplied kwargs.

    Plumbing keys are dropped and credentials redacted by ``summarize_value``
    itself, so nested argument bags get the same treatment as the top level.
    """
    if not isinstance(args, dict):
        return {}
    summarized = summarize_value(args)
    return summarized if isinstance(summarized, dict) else {}


def preview_text(value: Any, limit: int = MAX_STRING) -> str | None:
    """A short, safe preview of a text-ish value, or None when there is none."""
    if not isinstance(value, str) or not value.strip():
        return None
    return _clean_string(value, limit)
