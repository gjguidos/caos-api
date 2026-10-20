"""Runtime plumbing must not leak into a run's declared inputs.

``WorkflowTool._run`` receives the parsed CLI overrides as a nested ``kwargs``
bag, which ``normalize_inputs`` flattens. Without reserving the workflow tool's
own control flags, `--op=start --inputs=...` lands `op` and `inputs` in
``run.inputs`` and they become bindable as ``${inputs.op}``.
"""

from __future__ import annotations

from inspect import signature

from src.domains.agentverse.tools._helpers.normalize_inputs import normalize_inputs
from src.domains.agentverse.tools.workflow_tool import (
    RESERVED_WORKFLOW_INPUT_KEYS,
    WorkflowTool,
)


def _normalize(inputs: dict, kwargs: dict) -> dict:
    return normalize_inputs(
        inputs=inputs,
        kwargs=kwargs,
        reserved=RESERVED_WORKFLOW_INPUT_KEYS,
        ignore_blank_strings=False,
        ignore_none=False,
        strip_strings=False,
    )


class TestReservedKeys:
    def test_control_flags_do_not_become_inputs(self) -> None:
        # Mirrors the real shape: the manager passes parsed CLI flags nested
        # under `kwargs`, which normalize_inputs flattens.
        result = _normalize(
            {"image_input": "f_abc"},
            {
                "kwargs": {
                    "op": "start",
                    "inputs": "%7B%22image_input%22%3A%22f_abc%22%7D",
                    "raw_text": "@workflow --op=start",
                    "text": "some turn text",
                }
            },
        )

        assert set(result) == {"image_input", "text"}

    def test_every_run_parameter_is_reserved(self) -> None:
        # The point of deriving the set: a new `_run` parameter is reserved the
        # moment it is added, with no constant to keep in sync.
        run_params = set(signature(WorkflowTool._run).parameters) - {"self"}

        assert run_params <= RESERVED_WORKFLOW_INPUT_KEYS

    def test_execution_scaffolding_does_not_become_inputs(self) -> None:
        result = _normalize(
            {"pic": "f_abc"},
            {
                "context": {},
                "policy": None,
                "agent": object(),
                "raw_text": "@workflow --op=start",
            },
        )

        assert set(result) == {"pic"}

    def test_text_stays_bindable(self) -> None:
        # `${inputs.text}` is load-bearing for mini_workflow - `text` must not
        # be reserved.
        assert "text" not in RESERVED_WORKFLOW_INPUT_KEYS
        assert _normalize({}, {"text": "write me a poem"}) == {"text": "write me a poem"}

    def test_declared_inputs_win_over_plumbing(self) -> None:
        result = _normalize({"text": "declared"}, {"text": "from plumbing"})
        assert result["text"] == "declared"

    def test_reserving_never_blocks_an_explicit_input(self) -> None:
        # The denylist only filters runtime kwargs. A workflow that genuinely
        # declares an input named like a control flag can still receive it via
        # `--inputs={"op": ...}`.
        assert _normalize({"op": "mine"}, {"kwargs": {"op": "start"}}) == {"op": "mine"}

    def test_undeclared_extras_still_pass_through(self) -> None:
        # Reserving is a denylist, not an allowlist: arbitrary caller-supplied
        # kwargs remain available as inputs.
        result = _normalize({}, {"tone": "formal"})
        assert result == {"tone": "formal"}
