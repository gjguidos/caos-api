# --- Output contract for h_analyze --------------------------------------------
# Mutates ctx with:
#   ctx["brief"]            -> WHAT to produce (content spec: intent, tone, audience, length, acceptance)
#   ctx["actions_needed"]   -> Observability only; tool selection is @tool_key driven.
#   ctx["route_confidence"] -> Router confidence (0..1); we apply a floor to avoid "0.0" no-ops
#   ctx["signals"]          -> (optional) Routing/compliance metadata (e.g., {compliance:{...}, disambiguation:{...}})
#
# Quick mental model:
# - brief          -> content recipe for downstream writers/renderers
# - signals        -> knobs for router/policy/evaluator (compliance, ambiguity, etc.)
# - actions_needed -> observability only; the planner uses selected_tool
# ------------------------------------------------------------------------------
from src.domains.agentverse.agents.orchestrator.built_in_phases.h_analyze import (
    h_analyze as _h_analyze,
)


async def h_analyze(ctx, extra):
    return await _h_analyze("analyze", ctx, extra)
