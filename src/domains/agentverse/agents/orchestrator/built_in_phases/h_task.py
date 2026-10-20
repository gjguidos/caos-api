# --- Output contract for h_task ------------------------------------------------
# Mutates ctx with:
#   ctx["artifact"] -> {
#       "kind": "images" | "text" | "combined",
#       "content": List[ImageLike] | str | {"images": [...], "text": str, "step_texts": {...}},
#       "meta": {
#           "tool": str,
#           "addons": Dict,
#       },
#   }
# -------------------------------------------------------------------------------

from __future__ import annotations

from typing import Any

from src.domains.agentverse.agents.orchestrator.built_in_phases.helpers import (
    resolve_turn_context,
)
from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.tools.tool_manager_v1.artifact import build_task_artifact

try:
    from pydantic import BaseModel
except Exception:  # pragma: no cover
    BaseModel = None  # type: ignore


def _as_dict(x: Any) -> dict[str, Any]:
    return x if isinstance(x, dict) else {}


def _step_ref(step: Any) -> str | None:
    if isinstance(step, str):
        return step
    if isinstance(step, dict):
        ref = step.get("ref")
        return ref if isinstance(ref, str) and ref else None
    return None


async def h_task(ctx: dict, extra: dict):
    turn = resolve_turn_context(ctx, extra)

    agent = turn["agent"]
    tm = turn["tool_manager"]
    tool_kwargs = turn["tool_kwargs"]
    socket_emitter = turn["socket_emitter"]
    policy = extra.get("policy")
    sid = turn["session_id"]

    ctx["tool_kwargs"] = tool_kwargs
    ctx["_socket_emitter"] = socket_emitter
    ctx["_session_id"] = sid

    user_tx_clean = ctx.get("request", "") or ""
    steps = ((ctx.get("plan") or {}).get("steps") or []) or ["chat"]
    history = ctx.get("history") or []
    raw_line = (ctx.get("raw_request") or ctx.get("raw_text") or user_tx_clean or "").strip()

    AGENTVERSE_LOGGER.info(
        LogDomain.UNIVERSAL_ORCHESTRATOR,
        "[🧭 ORCH][🛠️ TASK] task_start",
        extra={
            "session_id": sid,
            "steps": steps,
            "request_len": len(user_tx_clean),
            "history_len": len(history),
        },
    )

    meta: dict[str, Any] = {}
    context: dict[str, Any] = {}
    tool_results: dict[str, Any] = {}
    invoker_addons: dict[str, Any] = {}

    try:
        common_kwargs = {
            "text": user_tx_clean,
            "raw_text": raw_line,
            "history": history,
            "session_id": sid,
            # `request` is the tool-manager's HTTP-request slot (wired onto each
            # tool as ``self.request`` for DI-container access). It must be the
            # real Starlette request bound on the agent — NOT the user text,
            # which already travels via ``text``/``raw_text``.
            "request": getattr(agent, "request", None),
            "policy": policy,
            "agent": agent,
            "allow_llm": True,
            "tool_kwargs": tool_kwargs,
            "overrides": {
                "cli_flags": (ctx.get("flags") or {}).get("user", {}) or {},
                "args": tool_kwargs,
                "persona_mode": (getattr(policy, "extras", {}) or {}).get("chat_persona_mode"),
                "pipeline_neutral": (getattr(policy, "extras", {}) or {}).get("pipeline_neutral"),
            },
            **tool_kwargs,
        }

        for step in steps:
            env = {}

            try:
                if isinstance(step, str):
                    ref = step
                    step_kwargs = {}
                else:
                    ref = step.get("ref")
                    step_kwargs = dict(step.get("kwargs") or {})

                if not ref:
                    continue

                exec_kwargs = {
                    **common_kwargs,
                    **step_kwargs,
                }

                AGENTVERSE_LOGGER.info(
                    LogDomain.UNIVERSAL_ORCHESTRATOR,
                    "[🧭 ORCH][🛠️ TASK] step_execute_start",
                    extra={
                        "session_id": sid,
                        "tool_ref": ref,
                    },
                )

                if tm and hasattr(tm, "execute"):
                    env = (
                        await tm.execute(
                            socket_emitter=socket_emitter,
                            key=ref,
                            **exec_kwargs,
                        )
                        or {}
                    )

            except Exception as e:
                AGENTVERSE_LOGGER.exception(
                    LogDomain.UNIVERSAL_ORCHESTRATOR,
                    "[🧭 ORCH][💥 TASK] step_execute_failed",
                    extra={
                        "session_id": sid,
                        "step": step,
                        "error": str(e),
                    },
                )
                env = {}

            env = _as_dict(env)
            step_addons = _as_dict(env.get("addons"))

            raw_env = env

            renderables = env.get("renderables")
            if not isinstance(renderables, list):
                renderables = []

            text = env.get("text") or env.get("message")
            if not isinstance(text, str):
                text = ""

            tool_results[ref] = {
                "raw": raw_env,
                "text": text,
                "renderables": renderables,
                "addons": step_addons,
                "images": env.get("images") or (env.get("result") or {}).get("images") or [],
            }

            if isinstance(env.get("context"), dict):
                context = {**context, **env["context"]}

            # Bubble the context_based tool's constructed prompt (carried on the
            # invoker envelope top level) up to the chat agent so it can persist
            # the expanded prompt — what the model actually saw — as this turn's
            # user history entry instead of the raw @tool command.
            if env.get("rendered_user"):
                ctx["rendered_user"] = env["rendered_user"]

            if step_addons:
                invoker_addons = {**invoker_addons, **step_addons}

            AGENTVERSE_LOGGER.info(
                LogDomain.UNIVERSAL_ORCHESTRATOR,
                "[🧭 ORCH][🛠️ TASK] step_execute_complete",
                extra={
                    "session_id": sid,
                    "tool_ref": ref,
                    "has_addons": bool(step_addons),
                    "has_context": isinstance(env.get("context"), dict),
                },
            )

            AGENTVERSE_LOGGER.debug(
                LogDomain.UNIVERSAL_ORCHESTRATOR,
                "[🧭 ORCH][🛠️ TASK] step_result_snapshot",
                extra={
                    "session_id": sid,
                    "tool_ref": ref,
                    "env_keys": list(env.keys()),
                    "result_keys": list(tool_results[ref].keys()),
                    "has_renderables": bool(renderables),
                    "renderable_count": len(renderables),
                    "text_len": len(text),
                    "context_keys": list((env.get("context") or {}).keys())
                    if isinstance(env.get("context"), dict)
                    else [],
                },
            )

    except Exception as e:
        AGENTVERSE_LOGGER.exception(
            LogDomain.UNIVERSAL_ORCHESTRATOR,
            "[🧭 ORCH][💥 TASK] task_execution_failed",
            extra={
                "session_id": sid,
                "error": str(e),
            },
        )

    step_refs = [ref for step in steps if (ref := _step_ref(step))]

    ctx["artifact"] = build_task_artifact(
        steps=step_refs,
        tool_results=tool_results,
        context=context,
        invoker_addons=invoker_addons,
        meta=meta,
    )

    artifact = ctx["artifact"]
    artifact_kind = artifact.get("kind")
    artifact_tool = artifact.get("meta", {}).get("tool")

    AGENTVERSE_LOGGER.info(
        LogDomain.UNIVERSAL_ORCHESTRATOR,
        "[🧭 ORCH][📦 TASK] artifact_built",
        extra={
            "session_id": sid,
            "steps": step_refs,
            "artifact_kind": artifact_kind,
            "artifact_tool": artifact_tool,
        },
    )

    AGENTVERSE_LOGGER.debug(
        LogDomain.UNIVERSAL_ORCHESTRATOR,
        "[🧭 ORCH][📦 TASK] artifact_context_snapshot",
        extra={
            "session_id": sid,
            "context_keys": list(context.keys()),
            "addon_keys": list(invoker_addons.keys()),
        },
    )

    return ctx
