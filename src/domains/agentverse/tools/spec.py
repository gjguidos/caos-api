from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Literal

from pydantic import BaseModel as _BM
from pydantic import Field, field_validator

from src.domains.agentverse.utils.canonicalize import canonicalize


class ToolOutputSpec(_BM):
    """A single typed output a tool declares it returns.

    Powers authoring-time binding of ``${outputs.<step_id>.<key>}`` in the
    workflow builder. ``path`` is a dot-path into the tool's ``env`` - the
    ``result`` object the tool returned - which the engine projects into the
    templating namespace under ``key``.
    """

    key: str  # author-facing handle, e.g. "answer"
    label: str  # display text, e.g. "Answer"
    type: Literal["string", "number", "boolean", "list", "object", "image"]
    path: str  # env-relative locator, e.g. "answer"


class FileSlotSpec(_BM):
    """A named file input slot a tool declares in `supports.files.slots`.

    Slots are binding targets for workflow `file_bindings`; `accepted_mimes`
    and `required` are advisory (builder UI only, not runtime-enforced).
    """

    name: str  # binding key, e.g. "reference"
    label: str  # builder display, e.g. "Reference image"
    accepted_mimes: list[str] | None = None
    required: bool = False


class FilesSupport(_BM):
    """Describes what a tool accepts in `supports.files`."""

    max_count: int = 1
    max_size_bytes: int = 200 * 1024 * 1024
    accepted_mimes: list[str] | None = None
    slots: list[FileSlotSpec] = Field(default_factory=list)


def _canon_tag(s: str) -> str:
    return canonicalize(s).replace("-", "_")


ToolScope = Literal["agent", "workflow", "system"]


class ToolSpec(_BM):
    key: str | None = None
    name: str
    description: str | None = None
    tool_class: str | None = None
    version: str | None = None

    # Why the tool is on the agent, which decides where it can be invoked from.
    # ``agent`` is the direct-invocation surface; ``workflow`` means the tool is
    # equipped only to satisfy an attached workflow's requirements and is runnable
    # by the workflow engine but not selectable via ``@<tool_key>``.
    scope: ToolScope = "agent"

    metadata: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)

    tags: list[str] = Field(default_factory=list)

    enabled: bool = True  # When False, tool is disabled and should not be loaded

    # ---------- NEW: rich UI fields ----------
    ui: dict[str, Any] = Field(default_factory=dict)
    # `validate_default=True` so `_outputs_from_meta` hydrates from
    # `metadata["outputs"]` even when `outputs` isn't passed explicitly.
    outputs: list[ToolOutputSpec] = Field(default_factory=list, validate_default=True)
    supports: dict[str, Any] = Field(default_factory=dict)

    # Source identifier (e.g. "mcp_proxy" for remote tools)
    source: str | None = None

    model_config = {"json_encoders": {set: list}}

    @property
    def files_support(self) -> FilesSupport | None:
        """Return a parsed FilesSupport if `supports.files` is truthy, else None."""
        raw = self.supports.get("files")
        if not raw:
            return None
        if isinstance(raw, FilesSupport):
            return raw
        if isinstance(raw, dict):
            return FilesSupport(**raw)
        return FilesSupport()

    @field_validator("metadata", mode="before")
    @classmethod
    def _metadata_to_dict(cls, v):
        if v in (None, ""):
            return {}
        if isinstance(v, dict):
            return v
        try:
            import json

            return json.loads(v)
        except Exception:
            return {"raw": v}

    @field_validator("config", mode="before")
    @classmethod
    def _config_to_dict(cls, v):
        if v is None:
            return {}
        try:
            if isinstance(v, _BM):
                return v.model_dump(exclude_none=True)
        except Exception:
            pass
        if isinstance(v, SimpleNamespace):
            return vars(v)
        if not isinstance(v, dict):
            raise TypeError("config must be a dict/BaseModel/Namespace")
        return v

    @field_validator("key", mode="before")
    @classmethod
    def _infer_key(cls, v, info):
        if v:
            return canonicalize(v)
        name = info.data.get("name")
        return canonicalize(name) if name else v

    # ---------- NEW: copy out common rich fields from metadata, if present ----------
    @field_validator("ui", mode="before")
    @classmethod
    def _ui_from_meta(cls, v, info):
        if v:
            return v
        meta = info.data.get("metadata") or {}
        return dict(meta.get("ui") or {})

    @field_validator("outputs", mode="before")
    @classmethod
    def _outputs_from_meta(cls, v, info):
        if v:
            return v
        meta = info.data.get("metadata") or {}
        out = meta.get("outputs") or []
        return list(out) if isinstance(out, list) else []

    @field_validator("supports", mode="before")
    @classmethod
    def _supports_from_meta(cls, v, info):
        if v:
            return v
        meta = info.data.get("metadata") or {}
        return dict(meta.get("supports") or {})

    @field_validator("tags", mode="before")
    @classmethod
    def _tags_list(cls, v):
        if not v:
            return []
        if isinstance(v, (list, tuple, set)):
            vals = {_canon_tag(str(x)) for x in v if str(x).strip()}
        else:
            vals = {_canon_tag(s) for s in str(v).split(",") if s.strip()}
        return sorted(vals)


class AgentToolRef(_BM):
    """Slim reference stored on the Agent document: just the registry key + agent-specific config.

    The key is the sole addressing contract and is required - the display name is not
    stored, it is read back from the registry at expansion time. Keys are canonicalized
    on the way in, so a reference that canonicalizes to nothing is rejected rather than
    stored as an unaddressable blank. Unrecognized fields are dropped.
    """

    key: str = Field(min_length=1, description="Canonical tool key from the tool registry")
    config: dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "ignore"}

    @field_validator("key", mode="before")
    @classmethod
    def _canonicalize_key(cls, v: Any) -> str:
        return canonicalize(str(v or ""))

    @field_validator("config", mode="before")
    @classmethod
    def _config_to_dict(cls, v: Any) -> dict[str, Any]:
        if v is None:
            return {}
        if isinstance(v, dict):
            return v
        return {}
