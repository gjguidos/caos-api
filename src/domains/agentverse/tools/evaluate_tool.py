from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.registries import tool_registry_instance as tool_registry
from src.domains.agentverse.tools.base import BaseTool, ToolConfig

QUESTION_RE = re.compile(r"\?$|^\s*(who|what|where|when|why|how|which|name)\b", re.I)

LLM_JUDGE_SYSTEM_PROMPT = """You are a strict evaluator. Return STRICT JSON only:
{
  "pass_fail": true|false,
  "score": number,
  "reasons": string,
  "criteria": ["..."]
}
"""


class EvaluateConfig(ToolConfig):
    default_criteria: list[str] = ["factually-correct", "concise-direct-answer"]
    max_tokens: int = 256
    temperature: float = 0.0


async def _invoke_llm_via_agent(
    agent,
    *,
    system_prompt: str,
    user_text: str,
    temperature: float | None = None,
) -> str:
    if agent is None or getattr(agent, "llm_chat_async", None) is None:
        raise RuntimeError("Agent has no llm_chat_async method")

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text},
    ]
    return await agent.llm_chat_async(messages=messages, temperature=temperature)


@tool_registry.register_tool(
    key="evaluate",
    name="Evaluation Tool",
    description="Scores an answer against a question & brief acceptance criteria.",
    version="1.2.0",
    metadata={
        "ownership": "system",
        "requires_collection": False,
        "config_schema": "src.domains.agentverse.tools.evaluate_tool.EvaluateConfig",
        "config": EvaluateConfig().model_dump(),
        "execution_mode": "hybrid",
        "llm_free": False,
        "route": "llm_only",
        "actions": ["evaluate.answer", "evaluate.acceptance"],
        "tool_class": "evaluation",
        "tags": ["builtin", "system"],
        "outputs": [
            {"key": "pass_fail", "label": "Pass/fail", "type": "boolean", "path": "pass_fail"},
            {"key": "score", "label": "Score", "type": "number", "path": "score"},
        ],
    },
)
class EvaluationTool(BaseTool):
    key = "evaluate"
    name = "Evaluation Tool"
    version = "1.2.0"
    required_dependencies: list[str] = []
    capabilities = ["evaluation", "quality_gate"]

    parameters = {
        "question": {"type": "string", "required": True},
        "answer": {"type": "string", "required": True},
        "brief": {"type": "object", "required": False},
        "allow_llm": {"type": "boolean", "required": False, "default": False},
        "policy": {"type": "object", "required": False},
        "agent": {"type": "object", "required": False},
    }

    def __init__(self, config: Any = None):
        super().__init__(config=config)
        self.config = (
            config if isinstance(config, EvaluateConfig) else EvaluateConfig(**(config or {}))
        )

    def _collect_criteria(self, brief: dict | None, question: str) -> list[str]:
        crit = set(self.config.default_criteria or [])
        acc = (brief or {}).get("acceptance") or {}

        for k in acc.get("must_have") or []:
            crit.add(str(k).strip().lower())

        for k in acc.get("prefer") or []:
            crit.add(str(k).strip().lower())

        if QUESTION_RE.search(question or ""):
            crit.add("factually-correct")
            crit.add("concise-direct-answer")

        return sorted(c for c in crit if c)

    def _check_concise_direct_answer(
        self, *, question: str, answer: str, brief: dict | None = None
    ) -> dict[str, Any]:
        words = len((answer or "").split())
        concise = words <= 30
        no_preamble = not re.match(
            r"(?i)^(the|here|it|well|sure|okay|so[, ]|let'?s)\b",
            (answer or "").strip(),
        )
        return {"ok": concise and no_preamble, "note": f"words={words}"}

    def _check_cite_source(
        self, *, question: str, answer: str, brief: dict | None = None
    ) -> dict[str, Any]:
        cited = bool(re.search(r"https?://|\[\d+\]", answer or ""))
        return {
            "ok": cited,
            "note": "contains URL or numeric citation" if cited else "no citation detected",
        }

    def _check_factually_correct(
        self, *, question: str, answer: str, brief: dict | None = None
    ) -> dict[str, Any]:
        return {"ok": None, "note": "requires judge or external verification"}

    def _check_audience_fit(
        self, *, question: str, answer: str, brief: dict | None = None
    ) -> dict[str, Any]:
        audience = [str(x).lower() for x in ((brief or {}).get("audience") or [])]
        if not audience:
            return {"ok": None, "note": "no audience specified"}

        text = (answer or "").lower()

        if "jedi" in audience or "jedis" in audience:
            markers = ["force", "jedi", "padawan", "balance", "wisdom"]
            hits = sum(1 for m in markers if m in text)
            return {"ok": hits >= 1, "note": f"audience_markers={hits}"}

        return {"ok": None, "note": "no deterministic audience heuristic for this audience"}

    def _check_instruction_following(
        self, *, question: str, answer: str, brief: dict | None = None
    ) -> dict[str, Any]:
        if not answer.strip():
            return {"ok": False, "note": "empty answer"}

        constraints = [str(x).lower() for x in ((brief or {}).get("constraints") or [])]
        max_chars = None

        for c in constraints:
            m = re.search(r"max[_ -]?chars[:=]\s*(\d+)", c)
            if m:
                max_chars = int(m.group(1))
                break

        if max_chars is not None and len(answer) > max_chars:
            return {"ok": False, "note": f"len={len(answer)} exceeds max_chars={max_chars}"}

        return {"ok": True, "note": "basic instruction-following check passed"}

    def _criterion_handlers(self) -> dict[str, Callable[..., dict[str, Any]]]:
        return {
            "concise-direct-answer": self._check_concise_direct_answer,
            "cite-source": self._check_cite_source,
            "factually-correct": self._check_factually_correct,
            "audience-fit": self._check_audience_fit,
            "instruction-following": self._check_instruction_following,
        }

    def _rule_based_checks(
        self,
        criteria: list[str],
        question: str,
        answer: str,
        brief: dict | None = None,
    ) -> dict[str, Any]:
        findings: dict[str, Any] = {}
        score = 0.0
        total = 0

        handlers = self._criterion_handlers()

        for criterion in criteria:
            handler = handlers.get(criterion)
            if handler is None:
                findings[criterion] = {"ok": None, "note": "no deterministic handler registered"}
                continue

            try:
                result = handler(question=question, answer=answer, brief=brief)
                ok = result.get("ok")
                note = result.get("note", "")

                findings[criterion] = {"ok": ok, "note": note}

                if ok is not None:
                    total += 1
                    if ok is True:
                        score += 1.0

            except Exception as e:
                findings[criterion] = {"ok": None, "note": f"handler error: {e}"}

        base = score / max(1, total)
        return {"base_score": base, "findings": findings}

    async def _llm_judge_via_agent(
        self,
        *,
        agent: Any,
        question: str,
        answer: str,
        criteria: list[str],
        temperature: float,
    ) -> dict[str, Any]:
        user_prompt = f"""Evaluate the following answer against the question and criteria.
Question: '''{question}'''
Answer: '''{answer}'''
Criteria: {json.dumps(criteria)}
Provide your evaluation in STRICT JSON format as described.
"""

        try:
            AGENTVERSE_LOGGER.info(
                LogDomain.TOOL_BAY,
                "[🧰 TOOL][🤖 EVAL] llm_judge_start",
                extra={
                    "tool_key": self.key,
                    "criteria_count": len(criteria),
                    "question_len": len(question or ""),
                    "answer_len": len(answer or ""),
                },
            )

            text = await _invoke_llm_via_agent(
                agent,
                system_prompt=LLM_JUDGE_SYSTEM_PROMPT,
                user_text=user_prompt,
                temperature=temperature,
            )
            text = (text or "").strip()
            if not text:
                return {}

            judged = json.loads(text)
            if isinstance(judged, dict) and "score" in judged:
                return judged

        except Exception as e:
            AGENTVERSE_LOGGER.warning(
                LogDomain.TOOL_BAY,
                "[🧰 TOOL][⚠️ EVAL] llm_judge_failed",
                extra={
                    "tool_key": self.key,
                    "error_type": type(e).__name__,
                    "error": str(e),
                },
            )

        return {}

    async def _run(
        self,
        *,
        question: str,
        answer: str,
        brief: dict | None = None,
        allow_llm: bool = False,
        policy: dict | None = None,
        agent: Any = None,
        **kwargs,
    ) -> dict[str, Any]:
        criteria = self._collect_criteria(brief, question)

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[🧰 TOOL][📝 EVAL] run_start",
            extra={
                "tool_key": self.key,
                "criteria_count": len(criteria),
                "allow_llm": allow_llm,
                "question_len": len(question or ""),
                "answer_len": len(answer or ""),
            },
        )

        rb = self._rule_based_checks(
            criteria=criteria,
            question=question,
            answer=answer,
            brief=brief,
        )

        judged: dict[str, Any] = {}
        has_llm = bool(agent and getattr(agent, "llm_chat_async", None))

        if allow_llm and has_llm:
            judged = await self._llm_judge_via_agent(
                agent=agent,
                question=question,
                answer=answer,
                criteria=criteria,
                temperature=float(self.config.temperature),
            )
        else:
            AGENTVERSE_LOGGER.info(
                LogDomain.TOOL_BAY,
                "[🧰 TOOL][🫧 EVAL] llm_judge_skipped",
                extra={
                    "tool_key": self.key,
                    "allow_llm": allow_llm,
                    "has_llm": has_llm,
                    "agent_type": type(agent).__name__ if agent is not None else None,
                },
            )

        score = float(judged.get("score", rb["base_score"]))
        pass_fail = bool(judged.get("pass_fail", score >= 0.5))
        reasons = judged.get("reasons") or ("rule-based heuristic" if not judged else "LLM judge")

        out = {
            "pass_fail": pass_fail,
            "score": round(score, 3),
            "reasons": reasons,
            "criteria": judged.get("criteria") or criteria,
            "findings": rb["findings"],
        }

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[🧰 TOOL][✅ EVAL] run_complete",
            extra={
                "tool_key": self.key,
                "pass_fail": pass_fail,
                "score": round(score, 3),
                "criteria_count": len(out["criteria"]),
                "finding_count": len(out["findings"]),
                "used_llm_judge": bool(judged),
            },
        )

        return {"success": True, "result": out}
