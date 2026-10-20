from src.domains.agentverse.agents.orchestrator.built_in_phases.h_evaluate import (
    h_evaluate as _h_evaluate,
)


async def h_evaluate(ctx, extra):
    return await _h_evaluate("evaluate", ctx, extra)
