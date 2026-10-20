from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class WriterToolConfig(BaseModel):
    """
    Writer assistance tool config.

    Controls how the writer tool drafts, rewrites and polishes text based on
    role, style, mood, audience and length constraints.
    """

    # --- common tool controls (mirrors IdeationTool / AuditorTool style) ---
    enabled: bool = True
    timeout: float = 30.0
    retries: int = 0
    retry_backoff: float = 0.5
    configurable: bool = True
    extra: dict[str, Any] = Field(
        default_factory=dict,
        description="Free-form extra config for future extensions.",
    )
    # Economy / billing-ish
    economy_enabled: bool = False
    economy_currency: str = "USD"
    pricing: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional pricing metadata for accounting / metering.",
    )

    # LLM behaviour (writing → more creative than auditing)
    max_tokens: int = 1200
    temperature: float = 0.7
    top_p: float = 1.0
    presence_penalty: float = 0.0
    frequency_penalty: float = 0.2

    # How much chat history the tool might look at
    history_window: int = 6

    # --- writer-specific knobs / modes ---

    default_mode: str = Field(
        "write",
        description=(
            "Default writer mode: "
            "write|rewrite|summarize|expand.\n"
            "- rewrite: text-driven rewriting\n"
            "- summarize: text-driven summarization\n"
            "- expand: text-driven expansion\n"
            "- short_form/long_form: stylistic variants layered on top"
        ),
    )
    allowed_modes: list[str] = Field(
        default_factory=lambda: ["write", "rewrite", "summarize", "expand"]
    )
    # ---- persona / tone controls ----

    default_role: str | None = Field(
        default=None,
        description="Writer role (e.g. 'copywriter', 'legal', 'ux-writer').",
    )
    default_style: str | None = Field(
        default="neutral",
        description="Base text style (e.g. 'formal', 'conversational', 'playful').",
    )
    default_mood: str | None = Field(
        default=None,
        description="Emotional mood (e.g. 'warm', 'urgent', 'calm').",
    )
    default_audience: str | None = Field(
        default=None,
        description="Target audience description (e.g. 'B2B buyers', 'teens').",
    )

    # ---- length constraints ----

    max_char_length: int | None = Field(
        default=None,
        description="Maximum character length for the output (None = no hard limit).",
    )

    prefer_word_limits_over_chars: bool = Field(
        default=True,
        description="If both char and word limits are set, prefer word limits.",
    )

    respect_agent_voice: bool = Field(
        default=True,
        description="If True, keep alignment with the agent's personality/role.",
    )
