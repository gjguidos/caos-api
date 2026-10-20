from __future__ import annotations

from typing import Any

from src.domains.agentverse.agents.orchestrator.built_in_phases.helpers import (
    first_forced_pure_step,
    resolve_turn_context,
)
from src.domains.agentverse.agents.roles.base import note
from src.domains.agentverse.logging.logger_v1 import AGENTVERSE_LOGGER, LogDomain


def _safe_text(v: Any) -> str:
    if isinstance(v, str):
        return v.strip()
    return ""


def _safe_scalar_text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    return str(v).strip()


def _extract_artifact_text(art: dict[str, Any]) -> str:
    if not isinstance(art, dict):
        return ""

    txt = art.get("text")
    if isinstance(txt, str) and txt.strip():
        return txt.strip()

    step_texts = art.get("step_texts")
    if isinstance(step_texts, dict):
        for _, v in step_texts.items():
            if isinstance(v, str) and v.strip():
                return v.strip()

    content = art.get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()

    if isinstance(content, dict):
        for k in ("text", "message", "markdown"):
            v = content.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()

        old_step_texts = content.get("step_texts")
        if isinstance(old_step_texts, dict):
            for _, v in old_step_texts.items():
                if isinstance(v, str) and v.strip():
                    return v.strip()

    return ""


def _build_eval_question(ctx: dict[str, Any], brief: dict[str, Any]) -> str:
    flags = (ctx.get("flags") or {}).get("user") or {}
    raw_request = _safe_scalar_text(ctx.get("raw_request"))
    request = _safe_scalar_text(ctx.get("request"))

    text = _safe_text(flags.get("text"))
    audience = _safe_text(flags.get("audience"))
    mood = _safe_text(flags.get("mood"))
    max_chars = _safe_scalar_text(
        flags.get("max_chars") or flags.get("max-chars") or flags.get("max_char_length")
    )

    intent = _safe_scalar_text(brief.get("intent")).lower()

    if intent.startswith("write.") and text:
        parts = [f"Write content about: {text}"]
        if audience:
            parts.append(f"Audience: {audience}")
        if mood:
            parts.append(f"Mood: {mood}")
        if max_chars:
            parts.append(f"Constraint: max_chars={max_chars}")
        return " | ".join(parts)

    return raw_request or request


async def h_evaluate(evaluate_tool, ctx, extra):
    turn = resolve_turn_context(ctx, extra)
    agent = turn["agent"]
    tm = turn["tool_manager"]
    tool_kwargs = turn["tool_kwargs"]
    socket_emitter = turn["socket_emitter"]
    sid = turn["session_id"]

    ctx["tool_kwargs"] = tool_kwargs
    ctx["_socket_emitter"] = socket_emitter
    ctx["_session_id"] = sid
    brief = ctx.get("brief") or {}
    q = _build_eval_question(ctx, brief)
    art = ctx.get("artifact") or {}
    verifications = ctx.get("verifications") or []
    policy = extra.get("policy") or {}

    AGENTVERSE_LOGGER.info(
        LogDomain.UNIVERSAL_ORCHESTRATOR,
        "[🧭 ORCH][📝 EVAL] evaluate_start",
        extra={
            "session_id": sid,
            "question_len": len(q or ""),
            "verification_count": len(verifications),
        },
    )

    # Image artifacts have no extractable text - treat as a pass rather than empty-answer fail.
    if (art.get("kind") == "images") or (
        art.get("kind") == "combined"
        and isinstance(art.get("images"), list)
        and art["images"]
        and not _extract_artifact_text(art)
    ):
        result = {
            "pass_fail": True,
            "score": 1.0,
            "reasons": "image output - text evaluation skipped",
            "criteria": ["image-produced"],
            "findings": {"image-produced": {"ok": True, "note": f"kind={art.get('kind')}"}},
        }
        ctx["eval"] = result
        AGENTVERSE_LOGGER.info(
            LogDomain.UNIVERSAL_ORCHESTRATOR,
            "[🧭 ORCH][🖼️ EVAL] image_artifact_skip",
            extra={"session_id": sid, "kind": art.get("kind")},
        )
        note(ctx, "evaluate", pass_fail=True, score=1.0, criteria=["image-produced"])
        return ctx

    answer = _extract_artifact_text(art)

    if not answer:
        result = {
            "pass_fail": False,
            "score": 0.0,
            "reasons": "empty answer",
            "criteria": ["non-empty"],
            "findings": {"non-empty": {"ok": False, "note": "len=0"}},
        }
        ctx["eval"] = result

        AGENTVERSE_LOGGER.info(
            LogDomain.UNIVERSAL_ORCHESTRATOR,
            "[🧭 ORCH][🛑 EVAL] empty_answer",
            extra={
                "session_id": sid,
                "pass_fail": False,
                "score": 0.0,
            },
        )

        note(ctx, "evaluate", pass_fail=False, score=0.0, criteria=result["criteria"])
        return ctx

    forced_pure = first_forced_pure_step(tm, ctx, q) if tm else None
    skip_tool_eval = bool(forced_pure)

    if skip_tool_eval:
        AGENTVERSE_LOGGER.info(
            LogDomain.UNIVERSAL_ORCHESTRATOR,
            "[🧭 ORCH][🫧 EVAL] tool_eval_skipped",
            extra={
                "session_id": sid,
                "forced_pure": forced_pure,
            },
        )

    result = None

    if not skip_tool_eval and tm and hasattr(tm, "execute"):
        try:
            AGENTVERSE_LOGGER.info(
                LogDomain.UNIVERSAL_ORCHESTRATOR,
                "[🧭 ORCH][🤖 EVAL] tool_eval_start",
                extra={
                    "session_id": sid,
                    "evaluate_tool": evaluate_tool,
                },
            )

            env = await tm.execute(
                socket_emitter=socket_emitter,
                key=evaluate_tool,
                question=q,
                answer=answer,
                brief=brief,
                allow_llm=True,
                policy=policy,
                agent=agent,
                tool_kwargs=tool_kwargs,
                **tool_kwargs,
            )

            if isinstance(env, dict):
                result = (env.get("result") or env.get("data") or {}) or {}

            AGENTVERSE_LOGGER.info(
                LogDomain.UNIVERSAL_ORCHESTRATOR,
                "[🧭 ORCH][🤖 EVAL] tool_eval_complete",
                extra={
                    "session_id": sid,
                    "has_result": bool(result),
                },
            )

        except Exception as e:
            AGENTVERSE_LOGGER.warning(
                LogDomain.UNIVERSAL_ORCHESTRATOR,
                "[🧭 ORCH][⚠️ EVAL] tool_eval_failed",
                extra={
                    "session_id": sid,
                    "error_type": type(e).__name__,
                    "error": str(e),
                },
            )
            result = None

    if not isinstance(result, dict) or not result:
        result = {
            "pass_fail": True,
            "score": 1.0,
            "reasons": "non-empty fallback",
            "criteria": ["non-empty"],
            "findings": {"non-empty": {"ok": True, "note": f"len={len(answer)}"}},
        }

        AGENTVERSE_LOGGER.debug(
            LogDomain.UNIVERSAL_ORCHESTRATOR,
            "[🧭 ORCH][🧾 EVAL] fallback_result_applied",
            extra={
                "session_id": sid,
                "answer_len": len(answer),
            },
        )

    findings = dict(result.get("findings") or {})

    for v in verifications:
        vf = (v.get("result") or {}).get("findings") or {}
        if isinstance(vf, dict):
            for k, obj in vf.items():
                if k not in findings:
                    findings[k] = obj

    if skip_tool_eval:
        findings["tool-eval-skipped"] = {
            "ok": True,
            "note": f"forced_pure={forced_pure}",
        }

    result["findings"] = findings
    ctx["eval"] = result

    AGENTVERSE_LOGGER.info(
        LogDomain.UNIVERSAL_ORCHESTRATOR,
        "[🧭 ORCH][🏁 EVAL] evaluate_complete",
        extra={
            "session_id": sid,
            "pass_fail": bool(result.get("pass_fail")),
            "score": result.get("score"),
            "criteria": result.get("criteria") or [],
            "finding_count": len(findings),
        },
    )

    note(
        ctx,
        "evaluate",
        pass_fail=bool(result.get("pass_fail")),
        score=result.get("score"),
        criteria=result.get("criteria") or [],
    )
    return ctx
