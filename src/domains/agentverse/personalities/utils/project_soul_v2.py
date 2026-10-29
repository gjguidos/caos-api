"""Deterministic V2 projection of an Agent Soul into model instructions.

Only behaviorally relevant fields that a Soul explicitly sets are projected, from
IdentityProfile, BasicProfile, CognitiveProfile and ExpressionProfile. Fields left at
their schema defaults are omitted, so a sparse legacy Soul is not padded with
generic traits it never declared.

Deliberately excluded:
- Speech synthesis (voice gender/speed/accent/language): not text behavior.
- Self-assessed capability or state (``intelligence``, ``emotional_depth``,
  ``attention_span``, ``learning_style``): would be unsupported cognitive claims.
- ``alignment``, ``aesthetic_feel`` and ``love_language``: ambiguous as instructions.
- Culinary, economic, personal, sensory, spiritual, cultural and metadata sections:
  biographical or simulation data with no defined conversational semantics.

Numeric traits become qualitative words; a score is a disposition, not a measurement.
Every projected trait is character only: it grants no capability or permission and
is not evidence of any fact.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel

from src.domains.agentverse.entities.agent_soul_protocol_parts.agent_soul_protocol import (
    AgentSoulProtocol,
)

_BASIC_STYLE = (
    ("tone", "Tone"),
    ("communication_style", "Communication style"),
    ("reasoning_style", "Reasoning style"),
    ("language_register", "Language register"),
    ("humor", "Humor"),
    ("bias", "Preference"),
)
_COGNITIVE_LEVELS = (
    ("curiosity_level", "Curiosity"),
    ("skepticism_level", "Skepticism"),
    ("creativity", "Creativity"),
    ("empathy_level", "Empathy"),
    ("optimism_level", "Optimism"),
    ("humor", "Playfulness"),
    ("introversion", "Reserve"),
)
_COGNITIVE_WORDS = (
    ("risk_tolerance", "Risk tolerance"),
    ("conflict_response", "In disagreement"),
    ("decision_biases", "Decision leaning"),
)
_EXPRESSION = (
    ("communication_style", "Expression"),
    ("assertiveness", "Assertiveness"),
    ("leadership_style", "Guidance style"),
    ("sentiment_baseline", "Baseline sentiment"),
    ("sarcasm_tendency", "Sarcasm"),
)
DISCLAIMER = (
    "These traits describe character and approach only. They grant no capability, "
    "permission or knowledge, and are not evidence of any fact."
)


def _set(section: BaseModel | None, field: str) -> Any:
    """A value the Soul explicitly declared; schema defaults count as absent."""
    if section is None or field not in section.model_fields_set:
        return None
    value = getattr(section, field)
    if value in (None, "", [], {}):
        return None
    return value


def _level(value: float) -> str:
    if value >= 0.67:
        return "high"
    if value <= 0.33:
        return "low"
    return "moderate"


def _join(values: Iterable[Any]) -> str:
    return "; ".join(str(v) for v in values)


def project_soul_v2(soul: AgentSoulProtocol) -> str:
    if not isinstance(soul, AgentSoulProtocol):
        raise TypeError("A registered Agent Soul is required")
    basic, identity = soul.basic_profile, soul.identity_profile
    cognitive, expression = soul.cognitive_profile, soul.expression_profile
    name = _set(basic, "name") or _set(identity, "name") or "this agent"
    lines = [f"## Soul: {name}", DISCLAIMER]

    identity_lines = []
    description = _set(basic, "description") or _set(identity, "description")
    if description:
        identity_lines.append(f"- Character: {description}")
    if archetype := _set(basic, "archetype"):
        identity_lines.append(f"- Archetype: {', '.join(archetype)}")
    if origin := _set(identity, "origin"):
        identity_lines.append(f"- Inspiration: {origin}")
    if purpose := _set(basic, "primary_function"):
        identity_lines.append(f"- Purpose: {purpose}")
    if identity_lines:
        lines += ["", "### Identity", *identity_lines]

    if principles := _set(basic, "key_principles"):
        lines += ["", "### Principles", *(f"- {p}" for p in principles)]
    if strengths := _set(basic, "core_strengths"):
        lines += ["", "### Strengths", f"- {_join(strengths)}"]

    style = [f"- {label}: {v}" for field, label in _BASIC_STYLE if (v := _set(basic, field))]
    style += [f"- {label}: {v}" for field, label in _EXPRESSION if (v := _set(expression, field))]
    if style:
        lines += ["", "### Communication", *style]

    disposition = [
        f"- {label}: {_level(float(v))}"
        for field, label in _COGNITIVE_LEVELS
        if isinstance(v := _set(cognitive, field), int | float)
    ]
    disposition += [
        f"- {label}: {v}" for field, label in _COGNITIVE_WORDS if (v := _set(cognitive, field))
    ]
    signature = _set(basic, "emotional_signature")
    if signature is not None:
        for field, label in (("empathy", "Warmth"), ("optimism", "Outlook")):
            if isinstance(v := getattr(signature, field), int | float):
                disposition.append(f"- {label}: {_level(float(v))}")
        if signature.trust_tone:
            disposition.append(f"- Trust tone: {signature.trust_tone}")
    if disposition:
        lines += ["", "### Disposition", *disposition]
    return "\n".join(lines)
