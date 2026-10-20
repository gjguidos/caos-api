from __future__ import annotations

from collections.abc import Awaitable, Mapping
from typing import Any, Protocol, TypedDict

from src.domains.agentverse.llm.helpers.protocols import ChatMessage


class WriterHints(TypedDict, total=False):
    """
    Small hint bag passed down to your ChatTool / LLM client.
    Structurally similar to AuditorHints / IdeationHints so the invoker
    can treat tools uniformly.
    """

    temperature: float
    top_p: float
    budget_tokens: int
    max_tokens: int
    response_format: str  # "markdown" / "text"
    max_words_hint: int
    seed: int


class ContextBundle(TypedDict):
    system_overrides: list[str]
    messages: list[ChatMessage]
    attachments: list[Any]
    evidence: list[Any]
    hints: WriterHints
    user_overrides: str
    usage_meta: dict[str, Any]


class ToolMemoryReader(Protocol):
    """
    Async callable used to fetch tool-owned memory.
    Implemented by the tool and passed in to avoid importing the tool here.
    """

    def __call__(self, *, session_id: str | None, limit: int = 50) -> Awaitable[list[dict]]: ...


# ---- helpers -----------------------------------------------------------------


def _clean_opt_str(opts: Mapping[str, Any], key: str, default: str = "") -> str:
    """
    Fetch a value from opts and normalize it into a stripped string.

    Notes:
    - This is intentionally lightweight (no coercion import), because this file
      sits on the "prompt assembly" side of the tool.
    - If you want stricter behavior, you can swap this to use your coercion helpers.
    """
    v = opts.get(key, default)
    if v is None:
        return ""
    s = str(v).strip()
    return s


def _coerce_tone_of_voice(opts: Mapping[str, Any]) -> list[str]:
    """
    Normalize tone_of_voice into list[str] for header rendering.

    Accepts common shapes:
    - ["clear", "confident"]
    - ["CLEAR", ToneOfVoice.CLEAR, ...]  (will extract enum values)
    - "clear,confident" or "clear confident"
    - None / "" -> []
    """
    v = opts.get("tone_of_voice")
    if v is None:
        return []

    # Already list-ish
    if isinstance(v, list | tuple | set):
        out: list[str] = []
        for x in v:
            # If it's an enum, extract its value
            if hasattr(x, "value"):
                s = str(x.value).strip()
            else:
                s = str(x).strip()
            if s:
                out.append(s)
        return out

    # String inputs: allow commas, otherwise split on whitespace.
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return []
        if "," in s:
            parts = [p.strip() for p in s.split(",")]
        else:
            parts = [p.strip() for p in s.split()]
        return [p for p in parts if p]

    # Fallback: check if it's an enum, otherwise best-effort stringify
    if hasattr(v, "value"):
        s = str(v.value).strip()
    else:
        s = str(v).strip()
    return [s] if s else []


def _active_line(label: str, value: str) -> str | None:
    cleaned = value.strip()
    if not cleaned or cleaned.lower() == "auto":
        return None
    return f"- {label}: {cleaned}"


def _section(title: str, lines: list[str]) -> str:
    return "\n".join([title, *lines])


def build_writer_task_block(opts: Mapping[str, Any]) -> str:
    role = _clean_opt_str(opts, "role", "writer")
    style = _clean_opt_str(opts, "style", "auto")
    mood = _clean_opt_str(opts, "mood", "auto")
    audience = _clean_opt_str(opts, "audience", "general")
    mode = _clean_opt_str(opts, "mode", "write")

    tone_list = _coerce_tone_of_voice(opts)
    tone_str = ", ".join(tone_list)

    task_lines = [
        line
        for line in (
            _active_line("mode", mode),
            _active_line("role", role),
        )
        if line
    ]

    style_lines = [
        line
        for line in (
            _active_line("audience", audience),
            _active_line("style", style),
            _active_line("mood", mood),
            _active_line("tone of voice", tone_str),
        )
        if line
    ]

    sections = [_section("TASK", task_lines or ["- mode: write"])]
    if style_lines:
        sections.append(_section("STYLE AND AUDIENCE", style_lines))

    return "\n\n".join(sections)


def _build_constraints_block(opts: Mapping[str, Any]) -> str:
    max_char_length = opts.get("max_char_length")
    lines = [f"- maximum characters: {max_char_length}"] if max_char_length is not None else []
    return _section("CONSTRAINTS", lines) if lines else ""


def _is_transform_mode(mode: str) -> bool:
    return mode in {"rewrite", "edit", "polish", "summarize", "summarise", "expand"}


def _build_input_block(opts: Mapping[str, Any]) -> str:
    mode = _clean_opt_str(opts, "mode", "write").lower()
    text = _clean_opt_str(opts, "text")
    if not text:
        return ""

    if text and _is_transform_mode(mode):
        return _section("SOURCE TEXT", [text])

    return _section("User input", [text])


def build_canonical_user(opts: Mapping[str, Any]) -> str:
    sections: list[str] = [build_writer_task_block(opts)]

    for block in (
        _build_constraints_block(opts),
        _build_input_block(opts),
    ):
        if block:
            sections.append(block)

    return "\n\n".join(sections).strip()


def compute_temperature(base: float, mode: str) -> float:
    """
    Derive an effective sampling temperature based on writer mode.

    Policy:
    - summarize -> lower temperature (tighter)
    - rewrite   -> medium (preserve meaning)
    - write -> higher (more generative), capped
    """
    mode = (mode or "write").lower().strip()

    if mode in {"summarize"}:
        return max(0.1, min(base, 0.5))

    if mode in {"rewrite"}:
        return max(0.2, min(base, 0.7))

    return max(0.3, min(base, 0.9))


def build_hints(
    *,
    cfg: Any,
    mode: str,
    seed: int | None,
) -> WriterHints:
    """
    Build the hint bag for the LLM layer.

    Expected cfg fields (optional):
    - temperature, top_p, budget_tokens, max_tokens, max_words_hint
    """
    base_temp = getattr(cfg, "temperature", 0.7)
    hints: WriterHints = {
        "temperature": compute_temperature(base_temp, mode),
        "top_p": getattr(cfg, "top_p", 1.0),
        "budget_tokens": getattr(cfg, "budget_tokens", getattr(cfg, "max_tokens", 1200)),
        "max_tokens": getattr(cfg, "max_tokens", 1200),
        "response_format": "markdown",
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
    cfg: Any,  # WriterToolConfig-ish
    history: list[ChatMessage] | None,
    session_id: str | None,
    tool_key: str,
) -> ContextBundle:
    """
    Builds the ChatTool-compatible context bundle used by the invoker.

    - Adds system message (writer-role / rule constraints)
    - Appends either provided history or tool memory
    - Computes hints (temperature tuned by mode)
    - Adds canonical user payload (header + topic/idea + optional source text)
    """

    messages: list[ChatMessage] = [{"role": "system", "content": system_msg}]

    if history:
        # Raw stored history entries may carry bookkeeping keys (e.g. `meta` with the
        # raw @tool command). They are stripped at the single outbound gate,
        # ChatLLMMixin._sanitize_messages, before reaching the model.
        messages.extend(history[-getattr(cfg, "history_window", 10) :])

    canonical_user = build_canonical_user(opts)

    mode = _clean_opt_str(opts, "mode", getattr(cfg, "default_mode", "write"))
    seed = opts.get("seed")

    hints = build_hints(
        cfg=cfg,
        mode=mode,
        seed=seed if isinstance(seed, int) else None,
    )

    return ContextBundle(
        system_overrides=[],
        messages=messages,
        attachments=[],
        evidence=[],
        hints=hints,
        user_overrides=canonical_user,
        usage_meta={
            "provider": "writer_tool",
            "mode": mode,
            "has_text": bool(_clean_opt_str(opts, "text")),
        },
    )
