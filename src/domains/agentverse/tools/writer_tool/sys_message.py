from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from src.domains.agentverse.agents.roles.rules.rulepack_spec import RulePackSpec, RuleSpec


def _render_rules_block(rules: Iterable[RuleSpec]) -> str:
    """
    Render the active rules as a plain-text block for the system prompt.

    Writer prompts should expose the constraints, not audit metadata. Rule IDs
    still travel in the evidence payload for traceability.
    """
    lines: list[str] = []
    for r in rules:
        description = str(r.description or "").strip()
        if description:
            lines.append(f"- {description}")
    if not lines:
        return (
            "No explicit rules were provided. In that case, write in a neutral, "
            "clear, concise and honest style without making strong legal or "
            "environmental claims."
        )
    return "\n".join(lines)


def _merge_writer_config(packs: Iterable[RulePackSpec]) -> dict:
    """
    Merge writer-specific metadata from rule packs.

    Mirrors _merge_auditor_config but reads the 'writer' view instead.
    First-non-empty wins for each key.
    """
    cfg: dict = {
        "tone_instructions": None,
        "usage_note": None,
    }
    for p in packs or []:
        writer_cfg = (p.default_config or {}).get("writer") or {}
        for key in ("tone_instructions", "usage_note"):
            if writer_cfg.get(key) and not cfg.get(key):
                cfg[key] = writer_cfg[key]
    return cfg


def _render_pack_summary(packs: Iterable[RulePackSpec]) -> str:
    """
    Small header listing which rule packs are in force, so the model
    understands it's combining multiple constraint sets.
    """
    items: list[str] = []
    for p in packs or []:
        name = p.name or ""
        label = p.label or ""
        domain = p.domain or ""
        parts = [part for part in (label or name, domain) if part]
        if parts:
            items.append(" - " + " | ".join(parts))
    if not items:
        return "Active rule packs: none"
    return "Active rule packs:\n" + "\n".join(items)


def _clean_hint(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "value"):
        value = value.value
    return str(value).strip()


def _render_secondary_hints(
    *,
    style: Any = None,
    mood: Any = None,
    audience: Any = None,
) -> str:
    hints = {
        "style": _clean_hint(style),
        "mood": _clean_hint(mood),
        "audience": _clean_hint(audience),
    }
    active = {key: value for key, value in hints.items() if value and value.lower() != "auto"}

    if not active:
        return ""

    lines = [
        "Brief-specific style hints:",
        "- Use these only when they fit the active tone and rules.",
    ]
    lines.extend(f"- {key}: {value}" for key, value in active.items())
    return "\n".join(lines)


def build_writer_system_message(
    *,
    rules: list[RuleSpec],
    packs: list[RulePackSpec],
    tone_of_voice: Iterable[Any] | None = None,
    style: Any = None,
    mood: Any = None,
    audience: Any = None,
) -> str:
    """
    General writer system message builder.

    - rules: the resolved RuleSpec list for this agent/context (across all selected packs).
    - packs: the RulePackSpec objects backing those rules (e.g. GDPR, green_claims).

    This is the generative mirror of build_auditor_system_message:
    same rules + packs, but used as *constraints for writing* instead of auditing.
    """
    rules_block = _render_rules_block(rules)
    pack_cfg = _merge_writer_config(packs)
    pack_summary = _render_pack_summary(packs)
    secondary_hints = _render_secondary_hints(
        style=style,
        mood=mood,
        audience=audience,
    )

    tone_instructions = pack_cfg.get("tone_instructions") or (
        "Use the rules below as hard constraints when generating text but do not mention them explicitly."
    )

    msg = f"""
Writer Tool instructions:
- Generate or transform copy for the requested mode.
- Return only the requested copy; do not add prefaces, audits, rationales, checklists, or rule commentary.
- Follow the active rule packs silently.

Priority order:
1. Truthfulness, source fidelity, and rule constraints.
2. User task, mode, audience, and length constraints.
3. Mechanical writing rules.
4. Fluency, warmth, and polish.

Context:
{pack_summary}

{secondary_hints}

Mechanical writing rules:
{tone_instructions}
{rules_block}

Mode behaviour:
1) write (topic/idea-driven drafting):
   - Use the user input idea as the main guidance.
   - Draft new text that fits the described tone and audience.
   - You may invent reasonable details, but keep them realistic and rule-aligned.

2) rewrite:
   - Preserve the source meaning and key facts unless they conflict with the rules.
   - Improve clarity, tone, structure, and style.
   - Do not add substantiation or stronger claims than the source provides.

3) summarize:
   - Shorten faithfully.
   - Keep core meaning and key facts.
   - Do not add new claims.

4) expand:
   - Elaborate from the source or brief.
   - Add useful structure, examples, and narrative only when consistent with the input.
   - Do not invent specific legal, scientific, privacy, or environmental claims.
""".strip()

    return msg
