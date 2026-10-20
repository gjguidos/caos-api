from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .protocols import ChatMessage


@dataclass
class ContextDelta:
    system_overrides: list[str] = field(default_factory=list)
    messages: list[ChatMessage] = field(default_factory=list)
    attachments: list[Any] = field(default_factory=list)
    evidence: list[Any] = field(default_factory=list)
    hints: dict[str, Any] = field(default_factory=dict)
    # Either a plain string prompt or, for multimodal tools (e.g. proxy tools
    # returning text + images), a list of Responses-API content parts.
    user_overrides: str | list[Any] | None = None
    post: list[dict[str, Any]] = field(default_factory=list)

    @property
    def rendered_user_text(self) -> str | None:
        """
        The text the model saw for the constructed user turn, regardless of
        whether the override was a plain string or a multimodal content-part
        list. Used to persist a faithful, text-only history entry (images are
        per-turn attachments and are not replayed). Returns ``None`` when there
        is no constructed prompt.
        """
        overrides = self.user_overrides
        if isinstance(overrides, str):
            return overrides or None
        if isinstance(overrides, list):
            parts = [
                p["text"]
                for p in overrides
                if isinstance(p, dict)
                and p.get("type") == "input_text"
                and isinstance(p.get("text"), str)
                and p["text"]
            ]
            return "\n".join(parts) or None
        return None


@dataclass
class PostResult:
    images: list[Any] = field(default_factory=list)
    presented_lines: list[str] = field(default_factory=list)
    addons: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
