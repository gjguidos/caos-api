from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AuditorToolConfig(BaseModel):
    """
    Generic rules-based auditing tool config.
    Works for green-claims, GDPR, etc. via rule domains / packs.
    """

    # --- common tool controls (mirrors IdeationTool style) ---
    enabled: bool = True
    timeout: float = 30.0
    retries: int = 0
    retry_backoff: float = 0.5

    extra: dict[str, Any] = Field(default_factory=dict)

    # Economy / billing-ish
    economy_enabled: bool = False
    economy_currency: str = "USD"
    pricing: dict[str, Any] = Field(default_factory=dict)

    # LLM behaviour (auditing → low creativity)
    max_tokens: int = 900
    temperature: float = 0.1
    top_p: float = 1.0
    presence_penalty: float = 0.0
    frequency_penalty: float = 0.0

    # How much chat history the tool might look at
    history_window: int = 4

    # --- auditing-specific knobs ---

    # Fallback domain if none is specified. e.g. "green_claims", "gdpr"
    default_domain: str | None = "green_claims"

    # Fallback pack ids if agent/role didn't provide any
    default_pack_ids: list[str] = Field(default_factory=list)

    # Allowed domains; empty = anything
    allowed_domains: list[str] = Field(default_factory=list)

    # If true, fail fast when no rules found for the requested domain
    require_rules: bool = True

    # Contract name we want the LLM to obey (for JSON output schemas)
    json_contract_name: str = "generic_audit_v1"

    # Whether to include full rule descriptions in system prompt
    include_rule_descriptions: bool = True

    # Whether to embed a short human-readable explanation alongside JSON
    # (can be ignored by ChatTool if it only reads JSON)
    return_human_summary: bool = False
