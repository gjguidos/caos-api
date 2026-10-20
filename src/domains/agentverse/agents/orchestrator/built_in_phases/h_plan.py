# --- Output contract for h_plan ------------------------------------------------
# Mutates ctx with:
#   ctx["plan"] -> {"steps": List[str]}
# -------------------------------------------------------------------------------
from __future__ import annotations

from src.domains.agentverse.agents.roles.base import note
from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.registries import tool_registry_instance
from src.domains.agentverse.utils.canonicalize import canonicalize

# Tool the orchestrator falls back to when no @tool_key was supplied (plain chat).
CHAT_TOOL_KEY = "chat"


def canonicalize_step(step: str) -> str:
    if not step:
        return step
    canon = canonicalize(step)
    return canon if canon in tool_registry_instance else step


async def h_plan(ctx, extra):
    selected_tool = ctx.get("selected_tool")
    conf = float(ctx.get("route_confidence") or 0.0)

    AGENTVERSE_LOGGER.info(
        LogDomain.UNIVERSAL_ORCHESTRATOR,
        "[🧭 ORCH][📑 PLAN] plan_start",
        extra={
            "selected_tool": selected_tool,
            "route_confidence": conf,
        },
    )

    if selected_tool:
        # Deterministic one-tool plan driven by the @tool_key selection.
        steps = [canonicalize_step(selected_tool)]
    else:
        # No @tool_key → plain chat. Hard-pin to the chat/converse tool.
        steps = [CHAT_TOOL_KEY]

    primary_step = steps[0] if steps else None

    ctx["plan"] = {
        "steps": steps,
        "resolved_steps": steps,
        "suggested_steps": [],
        "primary_step": primary_step,
    }

    note(
        ctx,
        "plan",
        steps=steps,
        resolved_steps=steps,
        suggested_steps=[],
        primary_step=primary_step,
    )

    AGENTVERSE_LOGGER.info(
        LogDomain.UNIVERSAL_ORCHESTRATOR,
        "[🧭 ORCH][📑 PLAN] plan_complete",
        extra={
            "steps": steps,
            "primary_step": primary_step,
        },
    )

    return ctx
