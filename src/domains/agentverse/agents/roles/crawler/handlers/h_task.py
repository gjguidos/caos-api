# --- Output contract for h_task ------------------------------------------------
# Mutates ctx with:
#   ctx["artifact"] -> {
#       "kind": "images" | "text",
#       "content": List[ImageLike] | str,
#       "meta": {
#           "tool": str,
#           "addons": Dict,
#       },
#   }
# -------------------------------------------------------------------------------


from src.domains.agentverse.agents.orchestrator.built_in_phases.h_task import h_task as _h_task


async def h_task(ctx, extra):
    return await _h_task(ctx, extra)
