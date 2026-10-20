from __future__ import annotations

from collections.abc import Awaitable, Mapping
from typing import Any, Protocol, TypedDict

from src.domains.agentverse.llm.helpers.protocols import ChatMessage

# ---- types -----------------------------------------------------------------


class AuditorHints(TypedDict, total=False):
    """
    Small hint bag passed down to your ChatTool / LLM client.
    Keep it structurally similar to IdeationHints so the invoker
    can treat tools uniformly.
    """

    temperature: float
    top_p: float
    budget_tokens: int
    max_tokens: int
    response_format: str  # "json"
    max_words_hint: int
    seed: int


class ContextBundle(TypedDict):
    system_overrides: list[str]
    messages: list[ChatMessage]
    attachments: list[Any]
    evidence: list[Any]
    hints: AuditorHints
    user_overrides: str
    usage_meta: dict[str, Any]


class ToolMemoryReader(Protocol):
    """
    Async callable used to fetch tool-owned memory.
    Implemented by the tool and passed in to avoid importing the tool here.
    """

    def __call__(self, *, session_id: str | None, limit: int = 50) -> Awaitable[list[dict]]: ...


# ---- helpers -----------------------------------------------------------------


def build_canonical_user(opts: Mapping[str, Any]) -> str:
    """
    Stable, LLM-friendly 'canonical user' line for the AuditorTool.

    We include:
      - domain (e.g. "green_claims", "gdpr")
      - pack_ids (the rule packs in force)
      - strict flag
      - the raw text to audit

    This stays as plain text; the system message already defines the JSON contract.
    """
    domain = str(opts.get("domain") or "generic").strip()
    pack_ids = list(opts.get("pack_ids") or [])
    strict = bool(opts.get("strict"))
    text = str(opts.get("text") or "")

    pack_csv = ", ".join(f'"{p}"' for p in pack_ids) if pack_ids else ""

    return (
        "AUDIT REQUEST\n"
        f'domain="{domain}"; pack_ids=[{pack_csv}]; strict={str(strict).lower()}.\n\n'
        "TEXT TO AUDIT:\n"
        f"{text}"
    ).strip()


def compute_temperature(base: float, strict: bool) -> float:
    """
    For auditing, we generally want low variance.

    - strict = True  -> even lower temp
    - strict = False -> still low, but a bit more freedom
    """
    if strict:
        return max(0.1, min(base, 0.25))
    return max(0.1, min(base, 0.4))


def build_hints(
    *,
    cfg: Any,  # AuditorToolConfig-ish
    strict: bool,
    seed: int | None,
) -> AuditorHints:
    """
    Build the hint bag for the LLM layer.

    Expected cfg fields:
      - temperature
      - top_p
      - budget_tokens
      - max_tokens
      - max_words_hint  (optional but nice to have)
    """
    hints: AuditorHints = {
        "temperature": compute_temperature(getattr(cfg, "temperature", 0.3), strict),
        "top_p": getattr(cfg, "top_p", 1.0),
        "budget_tokens": getattr(cfg, "budget_tokens", 2000),
        "max_tokens": getattr(cfg, "max_tokens", 800),
        "response_format": "json",
        "max_words_hint": getattr(cfg, "max_words_hint", 1000),
    }
    if isinstance(seed, int):
        hints["seed"] = seed
    return hints


# ---- main entrypoint ---------------------------------------------------------


async def assemble_context_bundle(
    *,
    system_msg: str,
    opts: Mapping[str, Any],
    cfg: Any,  # AuditorToolConfig-ish
    history: list[ChatMessage] | None,
    session_id: str | None,
    tool_key: str,
) -> ContextBundle:
    """
    Builds the ChatTool-compatible context bundle used by the invoker.

    - Adds system message (built from rules + rule packs)
    - Appends either provided history or tool memory
    - Computes hints (low temperature, JSON format)
    - Adds canonical user line with domain, packs, strict flag, and text
    """

    # Base system message
    messages: list[ChatMessage] = [{"role": "system", "content": system_msg}]

    # Append convo history (if present) or tool-centric memory
    if history:
        messages.extend(history[-getattr(cfg, "history_window", 10) :])

    canonical_user = build_canonical_user(opts)

    strict_flag = bool(opts.get("strict"))
    seed = opts.get("seed")  # optional, in case you ever want determinism

    hints = build_hints(
        cfg=cfg,
        strict=strict_flag,
        seed=seed if isinstance(seed, int) else None,
    )

    return ContextBundle(
        system_overrides=[],
        messages=messages,
        attachments=[],
        evidence=[],
        hints=hints,
        user_overrides=canonical_user,
        usage_meta={"provider": "auditor_tool"},
    )
