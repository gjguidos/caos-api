from __future__ import annotations

from src.domains.agentverse.registries import workflow_registry_instance
from src.domains.agentverse.workflows.spec import WorkflowSpec


@workflow_registry_instance.register(
    key="nested_mini_workflow_v1",
    name="Nested Mini Workflow Test (v1)",
    description="Parent workflow that runs mini_workflow_v1 as a single workflow step.",
    version="0.1.0",
    metadata={
        "tools": ["workflow"],
        "uses_workflows": ["mini_workflow_v1"],
    },
)
def nested_mini_workflow_v1() -> WorkflowSpec:
    steps = [
        {
            "id": "run_mini_workflow",
            "type": "tool",
            "command": "@workflow --workflow_key=mini_workflow_v1 --text=${inputs.text}",
        },
        {
            "id": "end",
            "type": "end",
            "depends_on": ["run_mini_workflow"],
        },
    ]

    return WorkflowSpec(
        key="nested_mini_workflow_v1",
        name="Nested Mini Workflow Test (v1)",
        description="Parent workflow that runs mini_workflow_v1 as a single workflow step.",
        version="0.1.0",
        definition={
            "execution_mode": "sequential",
            "steps": steps,
        },
        tags=["test", "workflow", "nested"],
        ui={"icon": "🧪", "category": "test"},
    )
