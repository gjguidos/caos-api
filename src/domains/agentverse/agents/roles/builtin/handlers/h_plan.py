from src.domains.agentverse.agents.orchestrator.built_in_phases.h_plan import (
    h_plan as _h_plan,
)
from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)


def h_plan(ctx, extra):
    AGENTVERSE_LOGGER.debug(
        LogDomain.UNIVERSAL_ORCHESTRATOR,
        "[🧭 ORCH][📑 PLAN] phase_enter",
        extra={"role": ctx.get("role")},
    )
    return _h_plan(ctx, extra)
