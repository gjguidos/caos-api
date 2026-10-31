"""Optional cognitive Tool declaration; tasks, bindings and authority remain separate."""

from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from src.domains.agentverse.reasoning.capability import PROVIDER, SIGNAL, VERSION
from src.domains.agentverse.reasoning.models import (
    REQUEST_CONTRACT,
    RESULT_CONTRACT,
    TASK_KEY_PATTERN,
)
from src.domains.agentverse.reasoning.tasks import PUBLISHED_FINGERPRINTS, builtin_tasks
from src.domains.agentverse.tools.spec import SynapticCapabilitySpec, SynapticToolSpec, ToolSpec

ExactTaskRef = Annotated[
    str,
    StringConstraints(pattern=TASK_KEY_PATTERN, max_length=100),
    Field(json_schema_extra={"enum": sorted(PUBLISHED_FINGERPRINTS)}),
]
DEFAULT_TASKS = ("manuscript.answer_extraction@1.0.0",)


class ReasoningToolConfig(BaseModel):
    """Published reasoning tasks this Agent may request. Execution is authorized separately."""

    # A restriction, never authority: only exact ``id@version`` references to published
    # tasks. No model, provider, prompt, grant or scope can be expressed; the provider
    # still checks publication, the pinned fingerprint and the caller's UTU/CPBX
    # authority on every invocation.
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    tasks: Annotated[list[ExactTaskRef], Field(min_length=1, max_length=8)] = Field(
        default_factory=lambda: list(DEFAULT_TASKS)
    )

    @model_validator(mode="after")
    def _published(self) -> Self:
        if len(set(self.tasks)) != len(self.tasks):
            raise ValueError("Duplicate reasoning task reference")
        unknown = [task for task in self.tasks if task not in PUBLISHED_FINGERPRINTS]
        if unknown:
            raise ValueError("Reasoning task reference is not a published task")
        return self


reasoning_tool = ToolSpec(
    key="reasoning",
    name="Reasoning",
    description=(
        "Bounded, advisory interpretation through published reasoning tasks. Results are "
        "structured proposals that authorize nothing. Discovery and attachment do not "
        "authorize execution."
    ),
    version="1.0.0",
    tool_class="cognitive",
    scope="agent",
    exposure="public",
    tags=["reasoning", "cognitive", "advisory"],
    config=ReasoningToolConfig().model_dump(),
    metadata={
        "ownership": "global",
        "execution_mode": "synaptic_only",
        "llm_free": False,
        "config_schema": "src.domains.agentverse.tools.reasoning_tool.spec.ReasoningToolConfig",
    },
    synaptic=SynapticToolSpec(
        capabilities=[
            SynapticCapabilitySpec(
                signal=SIGNAL,
                version=VERSION,
                provider=PROVIDER,
                access="granted",
                input_contract=f"{REQUEST_CONTRACT}@1",
                output_contract=f"{RESULT_CONTRACT}@1",
            )
        ]
    ),
    ui={
        "category": "reasoning",
        "admin": {
            "fields": {
                "tasks": {
                    "label": "Reasoning tasks",
                    "input": "multiselect",
                    "help": (
                        "Exact published task versions this Agent may request; "
                        "execution is authorized separately."
                    ),
                    "default": list(DEFAULT_TASKS),
                    # Descriptive only: never instructions, schemas, models or budgets.
                    "options": [
                        {"value": task.key(), "label": task.title, "description": task.description}
                        for task in builtin_tasks()
                    ],
                }
            }
        },
    },
)
