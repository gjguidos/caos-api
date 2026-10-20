# --- Output contract for h_analyze --------------------------------------------
# Mutates ctx with:
#   ctx["brief"]            -> WHAT to produce (content spec: intent, tone, audience, length, acceptance)
#   ctx["selected_tool"]    -> canonical key of the @tool_key-selected tool, or None
#   ctx["actions_needed"]   -> observability only (the selected tool key, or [])
#   ctx["route_confidence"] -> Router confidence (0..1); we apply a floor to avoid "0.0" no-ops
#   ctx["signals"]          -> (optional) Routing/compliance metadata
# ------------------------------------------------------------------------------
from __future__ import annotations

import re

from src.domains.agentverse.agents.orchestrator.built_in_phases.helpers import (
    deterministic_brief_from_tool,
    extract_selected_tool,
    resolve_turn_context,
)
from src.domains.agentverse.agents.roles.base import note
from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)

QUESTION_RE = re.compile(r"\?$|^\s*(who|what|where|when|why|how|which|name)\b", re.I)


async def h_analyze(analysis_tool, ctx, extra):
    """
    Analyze the incoming turn and shape routing signals for the orchestrator.

    Tool selection is driven exclusively by an ``@<tool_key>`` token in the raw
    message. When present, a deterministic one-tool brief is produced. Otherwise the
    plan is hard-pinned to the chat tool downstream, and the brief comes from the
    deterministic fallback unless ``extra["allow_llm_brief"]`` opts into an LLM call
    that shapes it further (it never selects tools).

    Side effects:
      - Mutates ctx with: brief, selected_tool, actions_needed, route_confidence, signals.
      - Emits a note() entry for observability.
    """
    turn = resolve_turn_context(ctx, extra)

    agent = turn["agent"]
    tm = turn["tool_manager"]
    tool_kwargs = turn["tool_kwargs"]
    socket_emitter = turn["socket_emitter"]

    text = (ctx.get("raw_request") or ctx.get("_raw_text") or ctx.get("request") or "").strip()

    AGENTVERSE_LOGGER.info(
        LogDomain.UNIVERSAL_ORCHESTRATOR,
        "[🧭 ORCH][🔍 ANALYZE] analyze_start",
        extra={
            "role": ctx.get("role"),
            "text_len": len(text),
        },
    )

    policy = extra.get("policy") or {}
    qth = float(getattr(policy, "quality_threshold", 0.8) or 0.8)

    policy_extras = getattr(policy, "extras", {}) or {}
    cfg_verifier_pack = policy_extras.get("verifier_pack")
    cfg_require_cites = bool(policy_extras.get("require_citations"))
    cfg_domain_hint = policy_extras.get("domain_hint")

    brief = None
    route_conf = 0.0
    signals = {}

    # ----- @tool_key selection (deterministic, the only invocation surface) -----
    selected_tool = extract_selected_tool(text, tm)

    if selected_tool:
        resolved = deterministic_brief_from_tool(
            tm,
            key=selected_tool,
            text=text,
            qth=qth,
        )

        brief = resolved["brief"]
        signals = resolved["signals"]

        ctx["selected_tool"] = selected_tool
        ctx["brief"] = brief
        ctx["actions_needed"] = [selected_tool]
        ctx["route_confidence"] = 0.95
        ctx["signals"] = signals

        AGENTVERSE_LOGGER.info(
            LogDomain.UNIVERSAL_ORCHESTRATOR,
            "[🧭 ORCH][🎯 ANALYZE] tool_key_selected",
            extra={
                "selected_tool": selected_tool,
            },
        )

        note(
            ctx,
            analysis_tool,
            intent=brief.get("intent"),
            actions=ctx["actions_needed"],
            route_confidence=ctx["route_confidence"],
            signals=signals,
        )

        AGENTVERSE_LOGGER.info(
            LogDomain.UNIVERSAL_ORCHESTRATOR,
            "[🧭 ORCH][🏁 ANALYZE] analyze_complete",
            extra={
                "selected_tool": selected_tool,
                "route_confidence": ctx["route_confidence"],
            },
        )
        return ctx

    # ----- No @tool_key: LLM analyze for brief-shaping only (no tool selection) --
    ctx["selected_tool"] = None

    if extra.get("allow_llm_brief", True) and tm and hasattr(tm, "execute"):
        try:
            AGENTVERSE_LOGGER.debug(
                LogDomain.UNIVERSAL_ORCHESTRATOR,
                "[🧭 ORCH][🤖 ANALYZE] llm_analysis_start",
            )

            env = await tm.execute(
                tool_kwargs=tool_kwargs,
                socket_emitter=socket_emitter,
                key="analyze",
                text=text,
                policy=policy,
                allow_llm=True,
                agent=agent,
            )

            if isinstance(env, dict):
                if env.get("success") is False:
                    raise RuntimeError(env.get("error") or "Analyze tool failed")

                raw = env.get("raw")
                if isinstance(raw, dict):
                    if raw.get("success") is False:
                        raise RuntimeError(
                            raw.get("error") or raw.get("message") or "Analyze tool failed"
                        )
                    res = raw.get("result") or raw.get("data") or raw
                else:
                    res = (env.get("result") or env.get("data") or {}) or {}

                brief = res.get("brief") or None
                route_conf = float(
                    (res.get("signals") or {}).get("confidence")
                    or res.get("route_confidence")
                    or 0.0
                )
                signals = res.get("signals") or {}

                AGENTVERSE_LOGGER.debug(
                    LogDomain.UNIVERSAL_ORCHESTRATOR,
                    "[🧭 ORCH][🤖 ANALYZE] llm_analysis_complete",
                    extra={
                        "route_confidence": route_conf,
                        "brief_intent": (brief or {}).get("intent")
                        if isinstance(brief, dict)
                        else None,
                    },
                )
        except Exception as e:
            AGENTVERSE_LOGGER.warning(
                LogDomain.UNIVERSAL_ORCHESTRATOR,
                "[🧭 ORCH][⚠️ ANALYZE] llm_analysis_failed",
                extra={
                    "error_type": type(e).__name__,
                    "error": str(e),
                },
            )

    if not brief:
        intent = "query" if QUESTION_RE.search(text or "") else "general"
        brief = {
            "intent": intent,
            "audience": [],
            "tone": [],
            "constraints": [],
            "length_hint": "short",
            "acceptance": {
                "must_have": [],
                "prefer": [],
                "quality_threshold": qth,
            },
            "missing": [],
            "risks": [],
        }

    comp = (signals.get("compliance") or {}).copy()
    if cfg_verifier_pack and not comp.get("verifier_pack"):
        comp["verifier_pack"] = str(cfg_verifier_pack)
    if cfg_require_cites and comp.get("require_citations") is None:
        comp["require_citations"] = True
    if cfg_domain_hint and not comp.get("domain"):
        comp["domain"] = str(cfg_domain_hint)
    if comp:
        signals = {**signals, "compliance": comp}

    route_conf = max(route_conf, 0.35)

    ctx["brief"] = brief
    ctx["actions_needed"] = []
    ctx["route_confidence"] = route_conf
    if signals:
        ctx["signals"] = signals

    note(
        ctx,
        analysis_tool,
        intent=brief.get("intent"),
        actions=[],
        route_confidence=route_conf,
        signals=signals,
    )

    AGENTVERSE_LOGGER.info(
        LogDomain.UNIVERSAL_ORCHESTRATOR,
        "[🧭 ORCH][🏁 ANALYZE] analyze_complete",
        extra={
            "selected_tool": None,
            "route_confidence": route_conf,
        },
    )

    return ctx
