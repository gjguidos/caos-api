from __future__ import annotations

from src.domains.agentverse.registries import workflow_registry_instance
from src.domains.agentverse.workflows.spec import WorkflowInputField, WorkflowSpec


@workflow_registry_instance.register(
    key="mini_workflow_v1",
    name="Mini Workflow (v1)",
    description="Minimal workflow used to test WorkflowTool registry resolution and execution.",
    version="0.1.0",
    metadata={
        "ownership": "global",
        "tools": ["writer", "draw"],
    },
)
def mini_workflow_v1() -> WorkflowSpec:
    steps = [
        {
            "id": "write",
            "type": "tool",
            "command": "@writer --mode=write --text=${inputs.text}",
            "label": "Write Product Description",
        },
        {
            "id": "review_description",
            "type": "pause",
            "depends_on": ["write"],
            "pause": {
                "prompt": "Review the generated product description before generating an image.",
            },
            "label": "Review Product Description",
        },
        {
            "id": "draw",
            "type": "tool",
            "depends_on": ["review_description"],
            "command": "@draw --prompt=${outputs.write.answer}",
            "label": "Draw Product Image",
        },
        {
            "id": "end",
            "type": "end",
            "depends_on": ["draw"],
            "label": "End Workflow",
        },
    ]

    return WorkflowSpec(
        key="mini_workflow_v1",
        name="Mini Workflow (v1)",
        description="Minimal workflow used to test WorkflowTool registry resolution and execution.",
        version="0.1.0",
        definition={
            "execution_mode": "sequential",
            "steps": steps,
        },
        tags=["test", "workflow", "minimal"],
        inputs=[
            WorkflowInputField(
                key="text",
                label="Text",
                input="textarea",
                required=True,
                help="Text the workflow will write up.",
            )
        ],
        ui={"icon": "🧪", "category": "test"},
    )
