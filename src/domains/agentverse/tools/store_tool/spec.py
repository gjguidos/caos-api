from __future__ import annotations

from src.domains.agentverse.tools.spec import SynapticCapabilitySpec, SynapticToolSpec

STORE_CREATE_SIGNAL = "store.create"
STORE_CREATE_VERSION = "1.0.0"
STORE_PROVIDER = "system.store"
# Separately grantable: holding it confers no general store.create access, and it
# can only create the identities pinned in reviewed server policy.
STORE_CREATE_PROVISIONED_SIGNAL = "store.create.provisioned"
STORE_CREATE_PROVISIONED_VERSION = "1.0.0"
# Separately grantable and only executable under the reviewed creation scope: writes
# exactly the candidate of one effective human approval, at most once per approval.
STORE_CREATE_APPROVED_SIGNAL = "store.create.approved"
STORE_CREATE_APPROVED_VERSION = "1.0.0"

# Synaptic only: deliberately absent from the V1 tool catalog/ToolManager, so the
# privileged write is reachable solely through CPBX with a live UTU permit.
STORE_TOOL_SPEC = SynapticToolSpec(
    capabilities=[
        SynapticCapabilitySpec(signal=STORE_CREATE_SIGNAL, access="system"),
        SynapticCapabilitySpec(signal=STORE_CREATE_PROVISIONED_SIGNAL, access="system"),
        SynapticCapabilitySpec(signal=STORE_CREATE_APPROVED_SIGNAL, access="system"),
    ]
)
