from __future__ import annotations

import hashlib
import logging
import random
import re
from typing import Any

from src.domains.agentverse.registries import tool_registry_instance as tool_registry
from src.domains.agentverse.tools.base import BaseTool, ToolConfig

logger = logging.getLogger("CTATool")

QUESTION_RE = re.compile(r"\?$|^\s*(who|what|where|when|why|how|which|name)\b", re.I)

# ----------------- CTA library -----------------
CTA_LIBRARY = {
    "one-liner": {"label": "Boil it down to one line", "actions": ["converse.shrink"]},
    "show-steps": {"label": "Show how I got this", "actions": ["converse.plan"]},
    "add-citations": {"label": "Attach sources", "actions": ["rag.search", "citations.enforce"]},
    "make-email": {"label": "Turn this into an email", "actions": ["compose.email"]},
    "make-tasks": {"label": "Turn this into tasks", "actions": ["converse.tasks"]},
    "translate": {"label": "Translate it", "actions": ["converse.translate"]},
    # Images
    "img-regenerate": {"label": "Regenerate with a different style", "actions": ["image.generate"]},
    "img-background": {"label": "Change background", "actions": ["image.edit"]},
    "img-aspect": {"label": "Try another aspect ratio", "actions": ["image.generate"]},
    # RAG / research
    "condense-bullets": {"label": "Summarize in 5 bullets", "actions": ["converse.summarize"]},
    "export-refs": {"label": "Export references", "actions": ["citations.export"]},
    # Code / technical
    "make-snippet": {"label": "Generate a code snippet", "actions": ["code.generate"]},
    "explain-like5": {"label": "Explain simply", "actions": ["converse.explain"]},
}


# ----------------- config -----------------
class CTAConfig(ToolConfig):
    persona: str = "friendly, concise"
    locale: str = "en"
    style: str = "panel"  # "panel" | "inline"
    max_words: int = 22
    allow_llm: bool = True
    # feature switches
    enable_images_cta: bool = True
    enable_research_cta: bool = True
    enable_format_cta: bool = True


# ----------------- helpers -----------------
def _hash_seed(*parts) -> int:
    h = hashlib.sha256(("|".join(str(p) for p in parts)).encode("utf-8")).hexdigest()
    return int(h[:8], 16)


def _artifact_kind(ctx_obj: dict | None) -> str:
    return (ctx_obj or {}).get("kind") or "text"


def _used_tool_from_art(art: dict | None) -> str:
    return (((art or {}).get("meta") or {}).get("tool") or "").lower()


def _wants_citations(signals: dict | None) -> bool:
    comp = (signals or {}).get("compliance") or {}
    return bool(comp.get("require_citations"))


def _is_query(request: str, brief: dict | None) -> bool:
    if brief and (brief.get("intent") or "").lower() == "query":
        return True
    return bool(QUESTION_RE.search(request or ""))


def _oxford_join(items, conj="or"):
    items = [str(i).strip() for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} {conj} {items[1]}"
    return f"{', '.join(items[:-1])}, {conj} {items[-1]}"


def _build_cta_items(
    artifact: dict, request: str, brief: dict | None, signals: dict | None
) -> list[dict]:
    """Score candidates based on what actually happened, pick up to 3 (diverse categories)."""
    tool = _used_tool_from_art(artifact)
    kind = _artifact_kind(artifact)
    is_query = _is_query(request, brief)
    wants_cites = _wants_citations(signals)

    pool = {
        # generic
        "one-liner": 0.5,
        "show-steps": 0.5,
        "add-citations": 0.2,
        "make-email": 0.3,
        "make-tasks": 0.3,
        "translate": 0.2,
        "condense-bullets": 0.2,
        # images
        "img-regenerate": 0.0,
        "img-background": 0.0,
        "img-aspect": 0.0,
        # tech
        "make-snippet": 0.0,
        "explain-like5": 0.2,
        "export-refs": 0.1,
    }

    if kind == "images":
        pool["img-regenerate"] += 3.0
        pool["img-background"] += 2.0
        pool["img-aspect"] += 1.5

    if is_query:
        pool["show-steps"] += 1.0
        pool["one-liner"] += 1.0
        pool["make-email"] += 0.2
        pool["condense-bullets"] += 0.5

    if wants_cites:
        pool["add-citations"] += 1.0
        pool["export-refs"] += 0.6

    # tool-based nudges (light)
    if "rag" in tool or "search" in tool:
        pool["add-citations"] += 1.0
        pool["condense-bullets"] += 0.7
    if "compose.email" in tool:
        pool["make-email"] -= 0.5
        pool["one-liner"] += 0.5
        pool["show-steps"] += 0.3
    if "chat" in tool or "converse" in tool:
        pool["one-liner"] += 0.4
        pool["show-steps"] += 0.4

    ranked = sorted(
        ((cid, w) for cid, w in pool.items() if cid in CTA_LIBRARY),
        key=lambda x: x[1],
        reverse=True,
    )[:6]

    def _category(cid):
        if cid.startswith("img-"):
            return "image"
        if cid in {"add-citations", "export-refs"}:
            return "evidence"
        if cid in {"one-liner", "show-steps", "condense-bullets", "translate"}:
            return "format"
        if cid in {"make-email", "make-tasks"}:
            return "ops"
        if cid in {"make-snippet", "explain-like5"}:
            return "tech"
        return "other"

    seen_cats, picked = set(), []
    for cid, _ in ranked:
        cat = _category(cid)
        # prefer diversity
        if cat in seen_cats and len(picked) < 2:
            pass
        else:
            seen_cats.add(cat)
        picked.append(cid)
        if len(picked) >= 3:
            break

    return [{"id": cid, **CTA_LIBRARY[cid]} for cid in picked if cid in CTA_LIBRARY][:3]


def _natural_cta_prompt(
    artifact: dict,
    request: str,
    items: list[dict],
    *,
    persona="friendly, concise",
    max_words=22,
    brief: dict | None = None,
) -> str:
    tool = _used_tool_from_art(artifact)
    kind = _artifact_kind(artifact)
    verbs = [it["label"].lower() for it in items]
    seed = _hash_seed(request, tool, kind, "|".join(verbs))
    rng = random.Random(seed)

    if kind == "images":
        templates = [
            "Want me to {ops} on this image?",
            "Next step for the image: {ops}?",
            "Should I {ops} for the image?",
        ]
    elif _is_query(request, brief):
        templates = [
            "Want me to {ops} for this answer?",
            "What should I do next: {ops}?",
            "Shall I {ops}?",
        ]
    else:
        templates = [
            "How should I help next: {ops}?",
            "Would you like me to {ops}?",
            "Next step: {ops}?",
        ]

    ops = _oxford_join(verbs)
    prompt = rng.choice(templates).format(ops=ops)
    if len(prompt.split()) > max_words:
        prompt = f"Would you like me to {ops}?"
    return prompt


async def _rewrite_with_llm(agent, text: str, *, persona: str, locale: str, max_words: int) -> str:
    """Use agent.llm to rewrite the CTA prompt. Return empty string on failure."""
    if agent is None or getattr(agent, "llm", None) is None:
        logger.info("[CTATool] No agent.llm; skipping LLM rewrite")
        return ""

    system = "Rewrite the user's instruction as a single warm question. Return only the question."
    user = (
        "Rewrite this as a single warm question, "
        f"under {max_words} words, persona={persona}, locale={locale}.\n\n"
        f"Original:\n{text}"
    )

    response = await agent.llm_chat_async(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
    )
    return response


# ----------------- tool -----------------
@tool_registry.register_tool(
    key="cta",
    name="CTA Tool",
    description="Chooses contextual call-to-action items and crafts a short, friendly question. Optionally rewrites via LLM.",
    version="1.0.0",
    metadata={
        "ownership": "system",
        "config_schema": "src.domains.agentverse.tools.cta_tool.CTAConfig",
        "config": CTAConfig().model_dump(),
        "execution_mode": "hybrid",
        "llm_free": False,
        "route": "invoker",
        "actions": ["converse.cta"],
        "tool_class": "cta",
        "tags": ["builtin", "system"],
        "outputs": [
            {"key": "prompt", "label": "CTA prompt", "type": "string", "path": "prompt"},
            {"key": "items", "label": "CTA items", "type": "list", "path": "items"},
        ],
        "ui": {"icon": "sparkles", "color": "#0ea5e9"},
    },
)
class CTATool(BaseTool):
    key = "cta"
    name = "CTA Tool"
    version = "1.0.0"
    required_dependencies: list[str] = []  # LLM optional
    capabilities = ["cta", "ux"]

    parameters = {
        # inputs from role
        "artifact": {"type": "object", "required": True},
        "request": {"type": "string", "required": True},
        "brief": {"type": "object", "required": False},
        "signals": {"type": "object", "required": False},
        # runtime knobs (override config)
        "persona": {"type": "string", "required": False},
        "locale": {"type": "string", "required": False},
        "style": {"type": "string", "required": False, "enum": ["panel", "inline", "buttons"]},
        "max_words": {"type": "integer", "required": False},
        "allow_llm": {"type": "boolean", "required": False},
        # ToolManager system-minimal wiring
        "policy": {"type": "object", "required": False},
        "agent": {"type": "object", "required": False},
    }

    def __init__(self, config: Any = None):
        super().__init__(config=config or CTAConfig())
        # normalize config type
        self.config = config if isinstance(config, CTAConfig) else CTAConfig(**(config or {}))

    async def _run(
        self,
        *,
        artifact: dict,
        request: str,
        brief: dict | None = None,
        signals: dict | None = None,
        persona: str | None = None,
        locale: str | None = None,
        style: str | None = None,
        max_words: int | None = None,
        allow_llm: bool | None = None,
        policy: dict | None = None,
        agent: Any = None,
        **_,
    ) -> dict[str, Any]:
        # resolve runtime → config defaults
        persona = (persona or self.config.persona or "friendly, concise").strip()
        locale = (locale or self.config.locale or "en").strip()
        style = (style or self.config.style or "panel").strip().lower()
        max_words = int(max_words or self.config.max_words or 22)
        allow_llm = bool(self.config.allow_llm if allow_llm is None else allow_llm)

        items = _build_cta_items(artifact, request, brief, signals)
        if not items:
            return {"success": True, "result": {"prompt": "", "items": [], "style": style}}

        base_prompt = _natural_cta_prompt(
            artifact, request, items, persona=persona, max_words=max_words, brief=brief
        )

        # Optional LLM rewrite
        rewritten = ""
        if allow_llm:
            logger.info("[CTATool] allow_llm=True; attempting LLM rewrite")
            rewritten = await _rewrite_with_llm(
                agent, base_prompt, persona=persona, locale=locale, max_words=max_words
            )
        else:
            logger.info("[CTATool] allow_llm=False; using base CTA prompt")

        prompt = (rewritten or base_prompt).rstrip(" ?") + "?"

        # normalize style to FE expected values
        fe_style = "buttons" if style != "inline" else "inline"

        result = {
            "prompt": prompt,
            "items": items,
            "style": fe_style,
        }
        return {"success": True, "result": result}
