from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from src.domains.agentverse.tools.ideation_tool.config import IdeationToolConfig

logger = logging.getLogger("ideation_tool.sys_message")


def _build_system(*, cfg: IdeationToolConfig, list_style: str) -> str:
    bullet_hint = "numbered (1) 2) 3)" if list_style == "numbered" else "bulleted (•)"
    return (
        "Generate strong, relevant ideas.\n"
        f"Output as {bullet_hint} lines. "
        "Plain text only. No JSON, no code fences. "
        "Each line must be one distinct idea. "
        "Never mention tools, triggers, or handles."
    )


def compose_system_message(*, cfg: IdeationToolConfig, opts: Mapping[str, Any]) -> str:
    list_style = opts.get("list_style") or getattr(cfg, "list_style", "numbered")
    lines: list[str] = [_build_system(cfg=cfg, list_style=list_style)]

    topic = (opts.get("topic") or "").strip()
    num_ideas = int(opts.get("num_ideas") or getattr(cfg, "default_num_ideas", 12))

    lines += [
        f"Generate {num_ideas} ideas for: {topic!r}.",
        f"Topic (must be explicit in every idea): {topic}.",
        "Do not produce generic creativity slogans; every line must clearly relate to the topic.",
        "Every idea must clearly depict or describe the topic scenario itself.",
        "The topic must be central to the image or action of the idea, not just mentioned in passing.",
        "If a line does not explicitly tie to the topic, discard it and produce another.",
        "Never mention tools, triggers, or handles like '@ideate'.",
    ]

    lines += [
        "Use the active persona voice already provided above.",
        "Do not switch to a generic default style.",
        "Preserve persona diction and tone in every line.",
        "Ignore any persona instruction about response structure that conflicts with this tool; output only the requested list lines.",
        "Let persona influence wording and rhythm, but keep each idea clear and useful.",
        "Every line must end with exactly one CTA in the form (CTA: ...).",
        "The CTA should invite engagement with the specific idea, not give generic life advice.",
    ]

    lines += [
        "PERSONA AMPLIFIER:",
        "The active persona must be strongly visible in wording, rhythm, syntax, and imagery.",
        "Let the persona shape how each idea sounds, but not what the task requires.",
        "Prefer distinctive phrasing over generic marketing language.",
        "Preserve signature sentence rhythm, worldview, and verbal texture from the active persona.",
        "Do not flatten the voice into neutral copywriting, corporate phrasing, or motivational filler.",
        "If the persona naturally uses inversion, aphorisms, deduction, theatricality, or poetic compression, allow that to appear in brief, readable form.",
        "Keep the persona vivid, but never let style obscure the topic or the usefulness of the idea.",
    ]

    lines += [
        "ANTI-FLATTENING GUARD:",
        "Avoid generic inspirational language, vague life lessons, or empty uplift.",
        "Avoid abstract lines that could fit any topic.",
        "Avoid phrases like 'teaches us', 'reminds us', 'invites reflection', or similar moral framing unless the topic genuinely demands it.",
    ]

    lines += [
        "TASK LEASH:",
        "Every line must still depict a concrete idea about the requested topic.",
        "The topic scenario must stay central in every line.",
        "Style may color the idea, but must not replace the idea.",
    ]

    mode = opts.get("mode")
    if mode == "social":
        lines.append("Favor platform-ready phrasings.")
    elif mode == "framework":
        lines.append(
            "You may weave frameworks such as AIDA, PAS, or 4P into phrasing while still outputting plain text."
        )
    elif mode == "cta-only":
        lines.append("Focus primarily on concise call-to-action lines.")
    elif mode == "tags-only":
        lines.append("Focus primarily on hashtag-rich idea hooks.")

    if opts.get("goal"):
        lines.append(f"Campaign goal: {opts['goal']}.")
    if opts.get("audience"):
        lines.append(f"Primary audience: {opts['audience']}.")
    if opts.get("tone"):
        lines.append(f"Tone hint (non-binding): {opts['tone']}.")
    if opts.get("brand"):
        lines.append(f"Brand hint (non-binding): {opts['brand']}.")
    if opts.get("region"):
        lines.append(f"Region: {opts['region']}.")
    if opts.get("platforms"):
        lines.append("Consider platform nuances for: " + ", ".join(opts["platforms"]) + ".")
    if opts.get("banned_words"):
        lines.append("Avoid these words/phrases: " + ", ".join(opts["banned_words"]) + ".")
    if opts.get("frameworks"):
        lines.append("Frameworks requested: " + ", ".join(opts["frameworks"]) + ".")
    if opts.get("mentions"):
        lines.append("Mentions to consider (optional): " + ", ".join(opts["mentions"]) + ".")
    if opts.get("constraints"):
        lines.append("Constraints: " + "; ".join(opts["constraints"]) + ".")

    lines.append(
        "Formatting:\n"
        f"- Use {'1) 2) 3) ...' if list_style == 'numbered' else '• bullets'}.\n"
        "- Keep each line self-contained and compact.\n"
        "- Include a few relevant hashtags.\n"
        "- Include exactly one CTA at the end of each line in the form (CTA: ...).\n"
        "- Do not add explanatory parenthetical text before the CTA.\n"
        "- Return only the list lines. No preface, no commentary."
    )

    return "\n".join(lines)
