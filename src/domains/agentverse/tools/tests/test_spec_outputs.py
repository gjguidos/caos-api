"""Tests for the declared-output contract on ``ToolSpec``.

A tool declares its bindable outputs under ``metadata["outputs"]``; the
``_outputs_from_meta`` validator hydrates them onto the top-level
``ToolSpec.outputs`` field as typed ``ToolOutputSpec`` entries, and the four-value
``type`` enum is validated.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.domains.agentverse.tools.spec import ToolOutputSpec, ToolSpec


class TestOutputsHydration:
    def test_hydrates_from_metadata(self) -> None:
        spec = ToolSpec(
            key="writer",
            name="Writer Tool",
            metadata={
                "outputs": [
                    {"key": "answer", "label": "Answer", "type": "string", "path": "answer"},
                ]
            },
        )

        assert len(spec.outputs) == 1
        out = spec.outputs[0]
        assert isinstance(out, ToolOutputSpec)
        assert (out.key, out.label, out.type, out.path) == ("answer", "Answer", "string", "answer")

    def test_top_level_field_takes_precedence_over_metadata(self) -> None:
        spec = ToolSpec(
            name="X",
            outputs=[{"key": "a", "label": "A", "type": "number", "path": "a"}],
            metadata={"outputs": [{"key": "b", "label": "B", "type": "string", "path": "b"}]},
        )
        assert [o.key for o in spec.outputs] == ["a"]

    def test_defaults_to_empty_list(self) -> None:
        assert ToolSpec(name="X").outputs == []

    def test_all_types_accepted(self) -> None:
        outputs = [
            {"key": k, "label": k, "type": t, "path": k}
            for k, t in [
                ("s", "string"),
                ("n", "number"),
                ("b", "boolean"),
                ("l", "list"),
                ("o", "object"),
            ]
        ]
        spec = ToolSpec(name="X", metadata={"outputs": outputs})
        assert [o.type for o in spec.outputs] == ["string", "number", "boolean", "list", "object"]

    def test_invalid_type_raises(self) -> None:
        with pytest.raises(ValidationError):
            ToolSpec(
                name="X",
                metadata={"outputs": [{"key": "a", "label": "A", "type": "json", "path": "a"}]},
            )
