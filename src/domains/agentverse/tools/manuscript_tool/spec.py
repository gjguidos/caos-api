"""Optional Tool declaration; resources, bindings and authority remain separate."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

from src.domains.agentverse.manuscripts.capability import OPERATIONS, PROVIDER, SIGNALS, VERSION
from src.domains.agentverse.tools.spec import SynapticCapabilitySpec, SynapticToolSpec, ToolSpec

ExactManuscriptRef = Annotated[
    str,
    StringConstraints(
        pattern=r"^[a-z][a-z0-9_]{0,63}@(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$",
        max_length=100,
    ),
]


class ManuscriptToolConfig(BaseModel):
    """A start-time resource preference, not visibility or execution authority.

    A future version can introduce multiple references without changing AgentToolRef.
    Existence and requester visibility remain provider checks on every invocation.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    default_manuscript: ExactManuscriptRef = "agent_creation_interview@1.0.0"


manuscript_tool = ToolSpec(
    key="manuscript",
    name="Manuscript",
    description=(
        "Deterministic, version-pinned interviews. Discovery and attachment do not "
        "authorize execution; completion returns an artifact without submitting it."
    ),
    version="1.0.0",
    tool_class="deterministic",
    scope="agent",
    exposure="public",
    tags=["interview", "deterministic"],
    config=ManuscriptToolConfig().model_dump(),
    metadata={
        "ownership": "global",
        "execution_mode": "synaptic_only",
        "llm_free": True,
        "config_schema": "src.domains.agentverse.tools.manuscript_tool.spec.ManuscriptToolConfig",
    },
    synaptic=SynapticToolSpec(
        capabilities=[
            SynapticCapabilitySpec(
                signal=SIGNALS[op],
                version=VERSION,
                provider=PROVIDER,
                access="granted",
                input_contract=f"caos.manuscript_session.{op}@1",
                output_contract="caos.manuscript_session_result@1",
            )
            for op in OPERATIONS
        ]
    ),
    ui={
        "category": "interview",
        "admin": {
            "fields": {
                "default_manuscript": {
                    "label": "Default Manuscript",
                    "input": "text",
                    "help": "Exact id@version; availability and execution are checked separately.",
                }
            }
        },
    },
)
