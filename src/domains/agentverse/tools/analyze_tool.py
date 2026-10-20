from __future__ import annotations

import json
from typing import Any, Literal, Self, TypedDict

from pydantic import BaseModel, Field, ValidationError, confloat

from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.registries import tool_registry_instance as tool_registry
from src.domains.agentverse.tools.base import BaseTool
from src.domains.agentverse.utils.coercion import coerce_json

PROMPT = """You are shaping a user request into a concise execution brief.

You will receive:
- user_request
- tool_catalog

Your most important task is to extract intent, audience, tone, constraints, acceptance criteria, and any missing facts.

Rules:
- every field must match its JSON type exactly
- audience, tone, constraints, facts_to_gather must always be arrays of strings
- citations_required must always be true or false
- eval_rubric must always be an object with keys: must, prefer, verifiers
- acceptance must always be an object with keys: must_have, prefer, quality_threshold
- quality_threshold must be a number between 0 and 1; put any prose in prefer or must_have
- disambiguation must always be an object with keys: score, issues, questions
- domain and jurisdiction must be a single string, or null when not applicable

Return STRICT JSON only.
"""


def _as_list_of_str(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str):
        s = value.strip()
        if not s or s.lower() in {"none", "null", "n/a", "na"}:
            return []
        return [part.strip() for part in s.split(",") if part.strip()]
    return [str(value).strip()] if str(value).strip() else []


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, int | float):
        return bool(value)
    if isinstance(value, str):
        s = value.strip().lower()
        if s in {"true", "yes", "1", "required"}:
            return True
        if s in {"false", "no", "0", "none", "null", "optional", ""}:
            return False
    return default


_NULLISH_STRINGS = {"", "none", "null", "n/a", "na"}


def _as_optional_str(value: Any) -> str | None:
    """Coerce an LLM-supplied value to a trimmed string, mapping null-ish values to None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, list | tuple):
        return ", ".join(_as_list_of_str(list(value))) or None
    text = str(value).strip()
    return None if text.lower() in _NULLISH_STRINGS else text


def _as_float(value: Any, default: float, *, lo: float = 0.0, hi: float = 1.0) -> float:
    """Coerce an LLM-supplied scalar to a bounded float, falling back to `default`."""
    if value is None or value == "":
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, parsed))


def _as_acceptance(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {
            "must_have": _as_list_of_str(value.get("must_have")),
            "prefer": _as_list_of_str(value.get("prefer")),
            "quality_threshold": _as_float(value.get("quality_threshold"), 0.8),
        }
    if isinstance(value, str):
        return {
            "must_have": [],
            "prefer": _as_list_of_str(value),
            "quality_threshold": 0.8,
        }
    return {
        "must_have": [],
        "prefer": [],
        "quality_threshold": 0.8,
    }


def _as_disambiguation(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {
            "score": _as_float(value.get("score"), 0.0),
            "issues": _as_list_of_str(value.get("issues")),
            "questions": _as_list_of_str(value.get("questions")),
        }
    return {
        "score": 0.0,
        "issues": [],
        "questions": [],
    }


def _as_eval_rubric(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {
            "must": _as_list_of_str(value.get("must")),
            "prefer": _as_list_of_str(value.get("prefer")),
            "verifiers": _as_list_of_str(value.get("verifiers")),
        }
    if isinstance(value, str):
        parts = _as_list_of_str(value)
        return {
            "must": [],
            "prefer": parts,
            "verifiers": [],
        }
    return {
        "must": [],
        "prefer": [],
        "verifiers": [],
    }


def _sanitize_analysis_payload(raw: dict[str, Any]) -> dict[str, Any]:
    raw = dict(raw or {})

    raw["intent"] = str(raw.get("intent") or "general").strip().lower()
    raw["audience"] = _as_list_of_str(raw.get("audience"))
    raw["tone"] = _as_list_of_str(raw.get("tone"))
    raw["constraints"] = _as_list_of_str(raw.get("constraints"))
    raw["facts_to_gather"] = _as_list_of_str(raw.get("facts_to_gather"))
    raw["acceptance"] = _as_acceptance(raw.get("acceptance"))
    raw["disambiguation"] = _as_disambiguation(raw.get("disambiguation"))
    raw["eval_rubric"] = _as_eval_rubric(raw.get("eval_rubric"))
    raw["citations_required"] = _as_bool(raw.get("citations_required"), default=False)

    length_hint = str(raw.get("length_hint") or "medium").strip().lower()
    raw["length_hint"] = length_hint if length_hint in {"short", "medium", "long"} else "medium"

    raw["domain"] = _as_optional_str(raw.get("domain")) or "general"
    raw["jurisdiction"] = _as_optional_str(raw.get("jurisdiction"))

    raw["route_confidence"] = _as_float(raw.get("route_confidence"), 0.0)
    return raw


async def _invoke_llm_via_agent(agent, *, system_prompt: str, user_text: str) -> str:
    if agent is None or getattr(agent, "llm", None) is None:
        raise RuntimeError("Agent has no LLM (agent.llm is None)")

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text},
    ]

    response = await agent.llm_chat_async(messages=messages)
    return response


_ALLOWED_INTENTS = {"query", "instruction", "copywriting", "general"}
_ALLOWED_LENGTHS = {"short", "medium", "long"}


class Acceptance(BaseModel):
    must_have: list[str] = Field(default_factory=list)
    prefer: list[str] = Field(default_factory=list)
    quality_threshold: float = 0.8


class Disambiguation(BaseModel):
    score: confloat(ge=0, le=1) = 0.0
    issues: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)


class EvalRubric(BaseModel):
    must: list[str] = Field(default_factory=list)
    prefer: list[str] = Field(default_factory=list)
    verifiers: list[str] = Field(default_factory=list)


class ParsedAnalysis(BaseModel):
    intent: str = "general"
    audience: list[str] = Field(default_factory=list)
    tone: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    length_hint: str = "medium"
    acceptance: Acceptance = Field(default_factory=Acceptance)
    route_confidence: confloat(ge=0, le=1) = 0.0
    disambiguation: Disambiguation = Field(default_factory=Disambiguation)
    domain: str = "general"
    jurisdiction: str | None = None
    facts_to_gather: list[str] = Field(default_factory=list)
    citations_required: bool = False
    eval_rubric: EvalRubric = Field(default_factory=EvalRubric)

    def normalize(self) -> Self:
        if self.intent not in _ALLOWED_INTENTS:
            self.intent = "general"
        if self.length_hint not in _ALLOWED_LENGTHS:
            self.length_hint = "medium"
        return self


class Brief(TypedDict, total=False):
    intent: Literal["copywriting", "query", "instruction", "general"]
    audience: list[str]
    tone: list[str]
    length_hint: Literal["short", "medium", "long"]
    constraints: list[str]
    acceptance: dict[str, Any]
    missing: list[str]
    risks: list[str]


class Signals(TypedDict, total=False):
    confidence: float
    compliance: dict[str, Any]
    disambiguation: dict[str, Any]


class ContextBlock(TypedDict, total=False):
    type: Literal["instruction_patch", "constraint_set", "domain_context", "safety_note"]
    title: str
    content: Any
    priority: float


Acceptance.model_rebuild()
Disambiguation.model_rebuild()
EvalRubric.model_rebuild()
ParsedAnalysis.model_rebuild()


@tool_registry.register_tool(
    key="analyze",
    name="Analyze Tool",
    description="LLM-only brief and acceptance extraction. No tool selection.",
    version="2.0.0",
    metadata={
        "tool_class": "analysis",
        "tags": ["builtin", "system"],
        "execution_mode": "llm_only",
        "llm_free": False,
        "route": "llm_only",
        # Intentionally empty: analyze returns a structured ParsedAnalysis, not a
        # single typed value worth exposing as a ${outputs.<step_id>.<key>} binding.
        "outputs": [],
    },
)
class AnalyzeTool(BaseTool):
    key = "analyze"
    name = "Analyze Tool"
    version = "2.0.0"

    parameters = {
        "text": {"type": "string", "required": True},
        "policy": {"type": "object", "required": False},
        "allow_llm": {"type": "boolean", "required": False, "default": True},
        "agent": {"type": "object", "required": False},
        "tool_catalog": {"type": "object", "required": False},
    }

    def __init__(self, config=None):
        super().__init__(config=config or {})

    async def _run(
        self,
        text: str,
        *,
        allow_llm: bool = True,
        policy: Any = None,
        agent: Any = None,
        tool_catalog: dict[str, Any] | None = None,
        **kwargs,
    ) -> dict[str, Any]:
        extras = getattr(policy, "extras", {}) if policy else {}
        compliance_cfg = (extras or {}).get("compliance", {}) or {}
        cfg_domain = compliance_cfg.get("domain", "general")
        cfg_require_cits = bool(compliance_cfg.get("require_citations", False))
        cfg_jurisdiction = compliance_cfg.get("jurisdiction")
        cfg_verifier_pack = compliance_cfg.get("verifier_pack")
        cfg_rubric = compliance_cfg.get("rubric") or {}

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[🧰 TOOL][🔍 ANALYZE] run_start",
            extra={
                "tool_key": self.key,
                "text_len": len(text or ""),
                "allow_llm": allow_llm,
                "tool_catalog_count": len(tool_catalog or {}),
            },
        )

        if not allow_llm:
            raise RuntimeError("AnalyzeTool v2 is LLM-only; set allow_llm=True or use v1.x")

        if agent is None or getattr(agent, "llm", None) is None:
            raise RuntimeError("AnalyzeTool v2 requires agent.llm to be available")

        analysis_input = {
            "user_request": text,
            "tool_catalog": tool_catalog or {},
        }

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[🧰 TOOL][🤖 ANALYZE] llm_invoke_start",
            extra={
                "tool_key": self.key,
                "input_keys": list(analysis_input.keys()),
            },
        )

        content = await _invoke_llm_via_agent(
            agent,
            system_prompt=PROMPT,
            user_text=json.dumps(analysis_input, ensure_ascii=False),
        )

        try:
            parsed_raw = coerce_json(content)
            parsed_raw = _sanitize_analysis_payload(parsed_raw)
            parsed = ParsedAnalysis(**parsed_raw).normalize()
        except (ValidationError, json.JSONDecodeError) as e:
            AGENTVERSE_LOGGER.exception(
                LogDomain.TOOL_BAY,
                "[🧰 TOOL][💥 ANALYZE] parse_failed",
                extra={
                    "tool_key": self.key,
                    "error_type": type(e).__name__,
                    "error": str(e),
                },
            )
            raise

        brief: Brief = {
            "intent": parsed.intent,
            "audience": parsed.audience,
            "tone": parsed.tone,
            "length_hint": parsed.length_hint,
            "constraints": parsed.constraints,
            "acceptance": parsed.acceptance.model_dump(),
            "missing": parsed.facts_to_gather,
            "risks": [],
        }

        signals: Signals = {
            "confidence": float(parsed.route_confidence or 0.0),
            "compliance": {
                "domain": cfg_domain,
                "require_citations": cfg_require_cits,
                "jurisdiction": cfg_jurisdiction,
                "verifier_pack": cfg_verifier_pack,
                "rubric": cfg_rubric,
            },
            "disambiguation": parsed.disambiguation.model_dump(),
        }

        context_blocks: list[ContextBlock] = [
            {
                "type": "instruction_patch",
                "title": "LLM analysis",
                "content": (
                    f"Intent={parsed.intent}; Tone={','.join(parsed.tone)}; "
                    f"Audience={','.join(parsed.audience)}; Length={parsed.length_hint}"
                ),
                "priority": 0.7,
            },
            {
                "type": "constraint_set",
                "title": "Acceptance criteria",
                "content": parsed.acceptance.model_dump(),
                "priority": 0.9,
            },
        ]

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[🧰 TOOL][✅ ANALYZE] run_complete",
            extra={
                "tool_key": self.key,
                "intent": parsed.intent,
                "route_confidence": parsed.route_confidence,
                "audience_count": len(parsed.audience),
                "tone_count": len(parsed.tone),
            },
        )

        return {
            "success": True,
            "result": {
                "brief": brief,
                "signals": signals,
                "context_blocks": context_blocks,
            },
        }
