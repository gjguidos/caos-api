from __future__ import annotations

from src.domains.agentverse.tools.spec import SynapticCapabilitySpec, SynapticToolSpec

STORE_CREATE_SIGNAL = "store.create"
STORE_CREATE_VERSION = "1.0.0"
STORE_PROVIDER = "system.store"

# Synaptic only: deliberately absent from the V1 tool catalog/ToolManager, so the
# privileged write is reachable solely through CPBX with a live UTU permit.
STORE_TOOL_SPEC = SynapticToolSpec(
    capabilities=[SynapticCapabilitySpec(signal=STORE_CREATE_SIGNAL, access="system")]
)
