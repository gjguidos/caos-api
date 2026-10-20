# --- Output contract for h_analyze --------------------------------------------
# Mutates ctx with:
#   ctx["brief"]            -> WHAT to produce (content spec: intent, tone, audience, length, acceptance)
#   ctx["actions_needed"]   -> Observability only; tool selection is @tool_key driven.
#   ctx["route_confidence"] -> Router confidence (0..1); we apply a floor to avoid "0.0" no-ops
#   ctx["signals"]          -> (optional) Routing/compliance metadata
#
# Quick mental model:
# - brief          -> content recipe for downstream writers/renderers
# - signals        -> knobs for router/policy/evaluator
# - actions_needed -> observability only; the planner uses selected_tool
# ------------------------------------------------------------------------------

from src.domains.agentverse.agents.orchestrator.built_in_phases.h_analyze import (
    h_analyze as _h_analyze,
)
from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)


async def h_analyze(ctx, extra):
    AGENTVERSE_LOGGER.debug(
        LogDomain.UNIVERSAL_ORCHESTRATOR,
        "[🧭 ORCH][🔍 ANALYZE] phase_enter",
        extra={"role": ctx.get("role")},
    )

    return await _h_analyze("analyze", ctx, extra)
