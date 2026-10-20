from __future__ import annotations

from typing import Any

from src.domains.agentverse.llm.invoker import llm
from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.tools.tool_manager_v1.envelope import as_envelope as _as_envelope


def _lift_images_from_tool_results(resp: dict) -> list[dict]:
    tr = resp.get("tool_results") or {}
    if not isinstance(tr, dict):
        return []

    for _, v in tr.items():
        if isinstance(v, dict):
            imgs = v.get("images")
            if isinstance(imgs, list) and imgs:
                return imgs

    return []


async def execute_invoker_path(
    manager: Any,
    *,
    key: str,
    meta: dict,
    text: str,
    history,
    session_id,
    policy,
    effective_ctx: dict,
) -> dict:

    AGENTVERSE_LOGGER.info(
        LogDomain.TOOL_BAY,
        "[🧰 TOOL][🌐 INVOKER] execute_start",
        extra={
            "tool_key": key,
            "text_len": len(text or ""),
        },
    )

    tool_call_text = text or ""

    AGENTVERSE_LOGGER.info(
        LogDomain.TOOL_BAY,
        "[🧰 TOOL][🤖 LLM] invoke_start",
        extra={
            "tool_key": key,
            "provider": key,
        },
    )

    overrides: dict[str, Any] = {
        "pipeline_neutral": True,
        "persona_mode": None,
        "cli_flags": (effective_ctx.get("flags") or {}).get("user", {})
        if isinstance(effective_ctx.get("flags"), dict)
        else {},
    }
    if files := effective_ctx.get("files"):
        overrides["args"] = {"files": files}

    resp = await llm(
        agent=manager.agent,
        user_text=tool_call_text,
        providers=[key],
        tool_manager=manager,
        policy=policy,
        session_id=session_id,
        history=history or effective_ctx.get("history") or [],
        context=effective_ctx,
        overrides=overrides,
    )

    imgs = _lift_images_from_tool_results(resp)

    if imgs:
        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            "[🧰 TOOL][🖼️ RESULT] images_detected",
            extra={
                "tool_key": key,
                "image_count": len(imgs),
            },
        )

        # Top level only: the response stays flat so `as_envelope` lifts it into
        # `result` whole. A `result` wrapper seeded here would become the entire
        # env, costing `answer`, `data`, and every declared output path with them.
        resp["images"] = imgs

    AGENTVERSE_LOGGER.info(
        LogDomain.TOOL_BAY,
        "[🧰 TOOL][📦 ENVELOPE] build_start",
        extra={"tool_key": key},
    )

    envelope = _as_envelope(resp)

    # as_envelope normalizes to a fixed key set and drops the llm `context`, so
    # explicitly carry the constructed prompt up on the envelope top level. The
    # orchestrator persists it as this turn's model-facing user history entry.
    resp_ctx = resp.get("context") or {}
    if isinstance(resp_ctx, dict) and resp_ctx.get("rendered_user"):
        envelope["rendered_user"] = resp_ctx["rendered_user"]

    AGENTVERSE_LOGGER.info(
        LogDomain.TOOL_BAY,
        "[🧰 TOOL][🌐 INVOKER] execute_complete",
        extra={"tool_key": key},
    )

    return envelope
