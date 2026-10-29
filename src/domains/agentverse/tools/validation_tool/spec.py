from __future__ import annotations

from src.domains.agentverse.tools.spec import SynapticCapabilitySpec, SynapticToolSpec

DATA_VALIDATION_SIGNAL = "data.validation"
DATA_VALIDATION_VERSION = "1.0.0"
DATA_VALIDATION_PROVIDER = "system.validation"

# Synaptic only: deliberately absent from the V1 tool catalog/ToolManager, so it is
# reachable solely through CPBX with a live UTU permit, never as a selectable tool.
DATA_VALIDATION_TOOL_SPEC = SynapticToolSpec(
    capabilities=[SynapticCapabilitySpec(signal=DATA_VALIDATION_SIGNAL, access="system")]
)
