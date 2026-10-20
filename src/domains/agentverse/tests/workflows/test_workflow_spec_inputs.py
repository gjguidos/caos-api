from typing import Any

from src.domains.agentverse.workflows.helpers.serialize import _serialize_workflow
from src.domains.agentverse.workflows.spec import WorkflowInputField, WorkflowSpec

_MINIMAL_DEFINITION: dict[str, Any] = {
    "key": "test_wf",
    "name": "Test",
    "execution_mode": "sequential",
    "steps": [],
}


def _spec(**kwargs: Any) -> WorkflowSpec:
    return WorkflowSpec(
        key="test_wf",
        name="Test",
        definition=_MINIMAL_DEFINITION,
        **kwargs,
    )


class TestWorkflowInputField:
    def test_serializes_required_fields(self) -> None:
        field = WorkflowInputField(key="text", label="Text")
        d = field.model_dump()
        assert d["key"] == "text"
        assert d["label"] == "Text"
        assert d["input"] == "text"
        assert d["required"] is False

    def test_serializes_all_declared_fields(self) -> None:
        field = WorkflowInputField(
            key="lang",
            label="Language",
            input="select",
            required=True,
            help="Pick a language",
            default="en",
            constraints={"min_length": 2},
            options=[{"label": "English", "value": "en"}],
        )
        d = field.model_dump()
        assert d["input"] == "select"
        assert d["required"] is True
        assert d["help"] == "Pick a language"
        assert d["default"] == "en"
        assert d["constraints"] == {"min_length": 2}
        assert d["options"] == [{"label": "English", "value": "en"}]

    def test_extra_fields_allowed(self) -> None:
        field = WorkflowInputField(key="x", label="X", component={"library": "shadcn"})
        d = field.model_dump()
        assert d["component"] == {"library": "shadcn"}


class TestWorkflowSpecInputs:
    def test_spec_without_inputs_is_valid(self) -> None:
        spec = _spec()
        assert spec.inputs == []

    def test_spec_with_inputs_stores_fields(self) -> None:
        spec = _spec(inputs=[WorkflowInputField(key="text", label="Text", required=True)])
        assert len(spec.inputs) == 1
        assert spec.inputs[0].key == "text"

    def test_serialize_no_inputs_has_empty_list(self) -> None:
        result = _serialize_workflow(_spec())
        assert result["inputs"] == []

    def test_serialize_with_inputs_appears_in_metadata(self) -> None:
        field = WorkflowInputField(
            key="text",
            label="Text",
            input="textarea",
            required=True,
            help="Text to write up.",
        )
        spec = _spec(inputs=[field])
        result = _serialize_workflow(spec)

        assert isinstance(result["inputs"], list)
        assert len(result["inputs"]) == 1
        entry = result["inputs"][0]
        assert entry["key"] == "text"
        assert entry["label"] == "Text"
        assert entry["input"] == "textarea"
        assert entry["required"] is True
        assert entry["help"] == "Text to write up."

    def test_serialize_multiple_inputs_preserves_order(self) -> None:
        spec = _spec(
            inputs=[
                WorkflowInputField(key="a", label="A"),
                WorkflowInputField(key="b", label="B"),
                WorkflowInputField(key="c", label="C"),
            ]
        )
        result = _serialize_workflow(spec)
        keys = [f["key"] for f in result["inputs"]]
        assert keys == ["a", "b", "c"]


class TestMiniWorkflowHasInputs:
    def test_mini_workflow_v1_declares_text_input(self) -> None:
        from src.domains.agentverse.registries import workflow_registry_instance
        from src.domains.agentverse.workflows.mini_workflow.mini_workflow import (  # noqa: F401
            mini_workflow_v1,
        )

        # get() returns the WorkflowSpec; check inputs are declared on the spec
        spec = workflow_registry_instance.get("mini_workflow_v1")
        assert len(spec.inputs) == 1
        assert spec.inputs[0].key == "text"
        assert spec.inputs[0].required is True

    def test_mini_workflow_v1_inputs_appear_in_registry_metadata(self) -> None:
        from src.domains.agentverse.registries import workflow_registry_instance
        from src.domains.agentverse.workflows.mini_workflow.mini_workflow import (  # noqa: F401
            mini_workflow_v1,
        )

        # _items stores the serialized metadata used by REST registry endpoints
        key = workflow_registry_instance._construct_key("mini_workflow_v1")
        meta = workflow_registry_instance._items[key].metadata
        inputs = meta.get("inputs", [])
        assert len(inputs) == 1
        assert inputs[0]["key"] == "text"
        assert inputs[0]["required"] is True
