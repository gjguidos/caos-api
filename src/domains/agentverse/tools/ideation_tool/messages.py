from __future__ import annotations

from collections.abc import Awaitable, Mapping
from typing import Any, Protocol, TypedDict

from src.domains.agentverse.llm.helpers.protocols import ChatMessage


class IdeationHints(TypedDict, total=False):
    temperature: float
    top_p: float
    budget_tokens: int
    max_tokens: int
    response_format: str
    max_words_hint: int
    seed: int


class ContextBundle(TypedDict):
    system_overrides: list[str]
    messages: list[ChatMessage]
    attachments: list[Any]
    evidence: list[Any]
    hints: IdeationHints
    user_overrides: str
    usage_meta: dict[str, Any]


class ToolMemoryReader(Protocol):
    """
    Async callable used to fetch tool-owned memory.
    Implemented by the tool and passed in to avoid importing the tool here.
    """

    def __call__(self, *, session_id: str | None, limit: int = 50) -> Awaitable[list[dict]]: ...


def build_canonical_user(opts: Mapping[str, Any]) -> str:
    """
    Stable, LLM-friendly 'canonical user' line summarizing the request.
    Keep this as plain text (no JSON) per your tool's contract.
    """
    topic = str(opts.get("topic") or "").strip()
    mode = str(opts.get("mode") or "cluster")
    num_ideas = int(opts.get("num_ideas") or 12)

    def _csv(key: str) -> str:
        seq = list(opts.get(key) or [])
        return ",".join(seq) if seq else ""

    goal = str(opts.get("goal") or "")
    audience = str(opts.get("audience") or "")
    tone = str(opts.get("tone") or "")
    region = str(opts.get("region") or "")

    return (
        f'Ideate: topic="{topic}"; mode="{mode}"; num_ideas={num_ideas}; '
        f'goal="{goal}"; audience="{audience}"; tone="{tone}"; '
        f"platforms={_csv('platforms')}; "
        f"frameworks={_csv('frameworks')}; "
        f'mentions={_csv("mentions")}; region="{region}". '
        "Return only the list as instructed."
    ).strip()


def compute_temperature(base: float, creativity: str | None) -> float:
    """
    Map human-friendly creativity to a temperature.
    """
    if creativity in (None, "", "medium"):
        return float(base)
    if creativity == "low":
        return 0.35
    return 0.9


def build_hints(
    *,
    cfg: Any,  # IdeationToolConfig-ish
    creativity: str | None,
    seed: int | None,
) -> IdeationHints:
    hints: IdeationHints = {
        "temperature": compute_temperature(cfg.temperature, creativity),
        "top_p": cfg.top_p,
        "budget_tokens": cfg.budget_tokens,
        "max_tokens": cfg.max_tokens,
        "response_format": "text",
        "max_words_hint": cfg.max_words_hint,
    }
    if isinstance(seed, int):
        hints["seed"] = seed
    return hints


async def assemble_context_bundle(
    *,
    system_msg: str,
    opts: Mapping[str, Any],
    cfg: Any,
    history: list[ChatMessage] | None,
    session_id: str | None,
    tool_key: str,
) -> ContextBundle:

    messages: list[ChatMessage] = [{"role": "system", "content": system_msg}]

    if history:
        # Raw stored history entries may carry bookkeeping keys (e.g. `meta` with the
        # raw @tool command). They are stripped at the single outbound gate,
        # ChatLLMMixin._sanitize_messages, before reaching the model.
        messages.extend(history[-cfg.history_window :])

    canonical_user = build_canonical_user(opts)

    hints = build_hints(
        cfg=cfg,
        creativity=str(opts.get("creativity") or "") or None,
        seed=opts.get("seed"),
    )

    return ContextBundle(
        system_overrides=[{"role": "system", "content": system_msg}],
        messages=messages,  # ✅ system goes here (same as writer)
        attachments=[],
        evidence=[],
        hints=hints,
        user_overrides=canonical_user,
        usage_meta={"provider": "ideation_tool"},
    )
