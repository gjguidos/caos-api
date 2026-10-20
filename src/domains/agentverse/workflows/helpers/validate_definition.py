"""
Save-time validation for workflow definitions.

Reuses the runtime engine's own validation primitives so a workflow that
passes here is structurally runnable:

1. ``WorkflowDefinition.model_validate`` — Pydantic shape.
2. ``validate_dag`` — dangling ``depends_on`` deps, cycles, duplicate ids.
3. Tool-reference existence — every ``type == "tool"`` step's command resolves
   to a key present in ``tool_registry_instance``.

The result is a structured list of issues so the canvas can highlight the
offending step / tool ids.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from src.domains.agentverse.entities.workflow import (
    WorkflowValidationIssue,
    WorkflowValidationResult,
)
from src.domains.agentverse.registries import tool_registry_instance
from src.domains.agentverse.tools.workflow_tool.dag import validate_dag
from src.domains.agentverse.tools.workflow_tool.helpers.templating import (
    MissingTemplateValueError,
    parse_single_reference,
)
from src.domains.agentverse.tools.workflow_tool.libs.engine.resolve_tool_key import (
    iter_step_tool_refs,
)
from src.domains.agentverse.tools.workflow_tool.models import WorkflowDefinition
from src.domains.agentverse.workflows.spec import WorkflowInputField


def _file_binding_warnings(
    wf: WorkflowDefinition,
    inputs: list[WorkflowInputField],
) -> list[WorkflowValidationIssue]:
    """Advisory check: a ``file_bindings`` slot referencing ``${inputs.<key>}``
    should point at a declared ``file`` input. Never blocks a save — the engine
    strict-fails at run time; this only powers builder hints."""
    fields_by_key = {f.key: f for f in inputs}
    warnings: list[WorkflowValidationIssue] = []

    for step in wf.steps:
        for slot, ref in (step.file_bindings or {}).items():
            try:
                key = parse_single_reference(ref)
            except MissingTemplateValueError:
                continue

            if not key.startswith("inputs."):
                continue

            input_key = key.removeprefix("inputs.")
            field = fields_by_key.get(input_key)

            if field is None:
                message = (
                    f"Step '{step.id}' slot '{slot}' references undeclared input '{input_key}'"
                )
            elif not field.is_file:
                message = (
                    f"Step '{step.id}' slot '{slot}' references input '{input_key}' "
                    f"which is not a file input (kind '{field.input}')"
                )
            else:
                continue

            warnings.append(
                WorkflowValidationIssue(
                    code="NON_FILE_INPUT_IN_SLOT",
                    message=message,
                    step_id=step.id,
                )
            )

    return warnings


def validate_workflow_definition(
    definition: dict[str, Any],
    *,
    inputs: list[WorkflowInputField] | None = None,
) -> WorkflowValidationResult:
    """Validate ``definition`` (a ``WorkflowDefinition``-shaped dict).

    Never raises for *validation* problems — returns a structured result.
    Callers that need a hard failure (POST/PATCH) translate an invalid result
    into a ``WorkflowValidationError`` (422). ``inputs`` (the spec's declared
    run inputs) only powers advisory warnings.

    A valid result carries ``required_tools``: the canonical keys the steps
    invoke. This is the only definition of a workflow's tool set — it is derived
    here, never declared, so it cannot drift from the DAG.
    """
    errors: list[WorkflowValidationIssue] = []

    # 1. Pydantic shape -----------------------------------------------------
    try:
        wf = WorkflowDefinition.model_validate(definition)
    except ValidationError as exc:
        for err in exc.errors():
            loc = ".".join(str(p) for p in err.get("loc", ()))
            errors.append(
                WorkflowValidationIssue(
                    code="SHAPE_INVALID",
                    message=f"{loc}: {err.get('msg')}" if loc else str(err.get("msg")),
                )
            )
        return WorkflowValidationResult(valid=False, errors=errors)

    # 2. DAG structure ------------------------------------------------------
    try:
        validate_dag(wf)
    except ValueError as exc:
        errors.append(WorkflowValidationIssue(code="DAG_INVALID", message=str(exc)))
        return WorkflowValidationResult(valid=False, errors=errors)

    # 3. Tool-reference existence ------------------------------------------
    required_tools: set[str] = set()
    for step, tool_key in iter_step_tool_refs(wf):
        if tool_key is None:
            errors.append(
                WorkflowValidationIssue(
                    code="TOOL_REF_MISSING",
                    message=f"Step '{step.id}' command does not reference a tool (@tool ...)",
                    step_id=step.id,
                )
            )
            continue

        if tool_key not in tool_registry_instance:
            errors.append(
                WorkflowValidationIssue(
                    code="TOOL_UNKNOWN",
                    message=f"Step '{step.id}' references unknown tool '{tool_key}'",
                    step_id=step.id,
                    tool_key=tool_key,
                )
            )
            continue

        required_tools.add(tool_key)

    # 4. Advisory: file_bindings slots vs declared inputs ------------------
    warnings = _file_binding_warnings(wf, inputs or [])

    return WorkflowValidationResult(
        valid=not errors,
        errors=errors,
        warnings=warnings,
        required_tools=sorted(required_tools),
    )
