from __future__ import annotations

from typing import Any

from src.domains.agentverse.agents.orchestrator.built_in_phases.helpers import (
    first_forced_pure_step,
    resolve_turn_context,
)
from src.domains.agentverse.agents.roles.base import note
from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)


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


def _store_artifact_text(art: dict[str, Any], text: str) -> dict[str, Any]:
    """
    Write back into the most canonical fields first, while preserving
    backward compatibility with older artifact contracts.
    """
    out = dict(art)

    if "text" in out or out.get("kind") == "text":
        out["text"] = text

    if isinstance(out.get("step_texts"), dict) and out["step_texts"]:
        first_key = next(iter(out["step_texts"]))
        out["step_texts"] = {**out["step_texts"], first_key: text}

    content = out.get("content")
    if isinstance(content, str):
        out["content"] = text
    elif isinstance(content, dict):
        out["content"] = {**content, "text": text}
    elif "content" not in out and out.get("kind") == "text":
        out["content"] = text

    return out


def _should_semantic_refine(
    *,
    brief: dict[str, Any],
    text: str,
    findings: dict[str, Any],
) -> bool:
    intent = (brief.get("intent") or "").strip().lower()
    if not text.strip():
        return False

    if intent.startswith("write."):
        return False

    if len(text.split()) < 20:
        return True

    for v in (findings or {}).values():
        if isinstance(v, dict) and v.get("ok") is False:
            return True

    if brief.get("audience"):
        return True

    return False


async def h_refine(ctx, extra):
    """
    Refine phase.

    Side effects:
      - may invoke the refine tool for semantic improvement
      - may short-circuit before doing so

    Mutates:
      ctx["artifact"]

    Returns:
      The same ctx dict, with possibly updated artifact text.
    """
    turn = resolve_turn_context(ctx, extra)

    tm = turn["tool_manager"]
    agent = turn["agent"]
    tool_kwargs = turn["tool_kwargs"]
    socket_emitter = turn["socket_emitter"]
    sid = turn["session_id"]

    ctx["tool_kwargs"] = tool_kwargs
    ctx["_socket_emitter"] = socket_emitter
    ctx["_session_id"] = sid
    art = ctx.get("artifact") or {}
    brief = ctx.get("brief") or {}
    findings = ctx.get("findings") or {}
    raw_request = ctx.get("raw_request") or ctx.get("request") or ""

    current_text = _extract_artifact_text(art)

    AGENTVERSE_LOGGER.info(
        LogDomain.UNIVERSAL_ORCHESTRATOR,
        "[🧭 ORCH][📝 REFINE] refine_start",
        extra={
            "session_id": sid,
            "artifact_kind": (art or {}).get("kind"),
            "text_len": len(current_text or ""),
        },
    )

    # ------------------------------------------------------------------
    # PURE PATH: deterministic short-circuit before any semantic refine
    # ------------------------------------------------------------------
    forced_pure = None
    if tm and raw_request:
        try:
            forced_pure = first_forced_pure_step(tm, ctx, raw_request)
        except Exception:
            forced_pure = None

    if forced_pure:
        AGENTVERSE_LOGGER.info(
            LogDomain.UNIVERSAL_ORCHESTRATOR,
            "[🧭 ORCH][🫧 REFINE] forced_pure_path",
            extra={
                "session_id": sid,
                "forced_step": forced_pure,
            },
        )
        note(
            ctx,
            "refine",
            mode="pure_forced",
            reason="forced_pure_step",
        )
        return ctx

    if not _should_semantic_refine(
        brief=brief,
        text=current_text,
        findings=findings,
    ):
        AGENTVERSE_LOGGER.info(
            LogDomain.UNIVERSAL_ORCHESTRATOR,
            "[🧭 ORCH][🫧 REFINE] no_semantic_refine",
            extra={
                "session_id": sid,
                "reason": "semantic_refine_not_needed",
            },
        )
        note(
            ctx,
            "refine",
            mode="no_semantic_refine",
            reason="semantic_refine_not_needed",
        )
        return ctx

    if not tm:
        AGENTVERSE_LOGGER.warning(
            LogDomain.UNIVERSAL_ORCHESTRATOR,
            "[🧭 ORCH][⚠️ REFINE] no_tool_manager",
            extra={
                "session_id": sid,
            },
        )
        note(
            ctx,
            "refine",
            mode="no_tool_manager",
            reason="missing_tool_manager",
        )
        return ctx

    AGENTVERSE_LOGGER.info(
        LogDomain.UNIVERSAL_ORCHESTRATOR,
        "[🧭 ORCH][🚀 REFINE] semantic_refine_start",
        extra={
            "session_id": sid,
            "text_len": len(current_text or ""),
            "has_findings": bool(findings),
        },
    )
    env = await tm.execute(
        socket_emitter=socket_emitter,
        key="refine",
        original_request=raw_request,
        draft=current_text,
        brief=brief,
        findings=findings,
        allow_llm=True,
        agent=agent,
        session_id=sid,
        tool_kwargs=tool_kwargs,
        **tool_kwargs,
    )

    result = (env.get("result") or {}) if isinstance(env, dict) else {}
    revised = (result.get("text") or "").strip()

    if revised and revised != current_text:
        ctx["artifact"] = _store_artifact_text(art, revised)

        AGENTVERSE_LOGGER.info(
            LogDomain.UNIVERSAL_ORCHESTRATOR,
            "[🧭 ORCH][✅ REFINE] semantic_refine_changed",
            extra={
                "session_id": sid,
                "before_len": len(current_text or ""),
                "after_len": len(revised or ""),
            },
        )

        note(
            ctx,
            "refine",
            mode="tool_semantic",
        )
    else:
        AGENTVERSE_LOGGER.info(
            LogDomain.UNIVERSAL_ORCHESTRATOR,
            "[🧭 ORCH][🟡 REFINE] semantic_refine_no_change",
            extra={
                "session_id": sid,
                "text_len": len(current_text or ""),
            },
        )

        note(
            ctx,
            "refine",
            mode="tool_semantic_no_change",
        )

    return ctx
