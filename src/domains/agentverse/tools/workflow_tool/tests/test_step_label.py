"""A step's authored `label` survives validation into the engine model.

Workflow specs carry their `definition` as a raw dict, so a label the builder
authored reaches the UI even if the model drops it - but anything that validates
into `WorkflowDefinition` and re-serializes (resolution snapshots, stored
definitions) would silently lose it. The field has to be declared to round-trip.
"""

from __future__ import annotations

from src.domains.agentverse.tools.workflow_tool.models import WorkflowDefinition, WorkflowStep
from src.domains.agentverse.workflows.mini_workflow.mini_workflow import mini_workflow_v1


class TestStepLabelRoundTrips:
    def test_label_survives_a_model_round_trip(self) -> None:
        step = WorkflowStep(
            **{"id": "write", "type": "tool", "command": "@writer --text=a", "label": "Write It"}
        )

        assert step.label == "Write It"
        assert step.model_dump()["label"] == "Write It"

    def test_label_is_optional(self) -> None:
        assert WorkflowStep(id="end", type="end").label is None

    def test_a_whitespace_only_label_is_no_label(self) -> None:
        """Matches `id`/`depends_on`, which also strip; the frontend falls back to
        `id` on None, so blank text must not reach the canvas as a real label."""
        assert WorkflowStep(id="end", type="end", label="   ").label is None

    def test_a_label_is_stripped(self) -> None:
        assert WorkflowStep(id="end", type="end", label="  End Workflow  ").label == "End Workflow"

    def test_authored_labels_survive_definition_validation(self) -> None:
        definition = WorkflowDefinition(**mini_workflow_v1().definition)

        assert [s.label for s in definition.steps] == [
            "Write Product Description",
            "Review Product Description",
            "Draw Product Image",
            "End Workflow",
        ]
