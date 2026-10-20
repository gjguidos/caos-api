from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.base.config.config import settings
from src.domains.agentverse.agents.orchestrator.context import PhaseHandler
from src.domains.agentverse.agents.orchestrator.phases import Phase

# Dropped unless ORCHESTRATOR_QUALITY_PHASES_ENABLED. They cost an LLM round-trip
# each without contributing to the reply.
QUALITY_PHASES = frozenset({Phase.REFINE.value, Phase.EVALUATE.value})


def apply_quality_gate(
    handlers: Mapping[str, PhaseHandler],
    extra: dict[str, Any],
) -> dict[str, PhaseHandler]:
    """Apply ORCHESTRATOR_QUALITY_PHASES_ENABLED to one turn's orchestration inputs.

    Two things ride on the same flag, so they are decided together here rather
    than read from settings at each site:

    - the refine and evaluate handlers are dropped from the map. Removing the
      handler rather than the phase preserves the loop's no-op accounting:
      phase_log and metrics still record the phases, marked skipped.
    - ``extra["allow_llm_brief"]`` tells the analyze phase whether to spend an
      LLM call shaping the brief. Its deterministic brief is what plan and
      ``@tool`` routing actually consume.

    Mutates ``extra``, the channel every phase already reads its per-turn inputs
    from (policy, tool_manager, tool_kwargs).

    The flag is read at call time, not bound at import, so tests and runtime
    config changes both take effect.
    """
    enabled = settings.orchestrator.orchestrator_quality_phases_enabled
    extra["allow_llm_brief"] = enabled

    if enabled:
        return dict(handlers)
    return {key: handler for key, handler in handlers.items() if key not in QUALITY_PHASES}
