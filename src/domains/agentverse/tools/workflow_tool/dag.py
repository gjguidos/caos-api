from __future__ import annotations

from copy import deepcopy

from src.domains.agentverse.tools.workflow_tool.models import (
    StepRuntime,
    WorkflowDefinition,
    WorkflowRun,
    WorkflowStep,
)


def normalise_sequential_dependencies(wf: WorkflowDefinition) -> WorkflowDefinition:
    """
    Adds implicit previous-step dependency when a step has no explicit dependencies.

    This preserves the current default behavior:
    workflows are sequential unless dependencies/next edges are explicitly declared.
    """
    wf = deepcopy(wf)

    previous: str | None = None

    for step in wf.steps:
        if not step.depends_on and previous:
            step.depends_on = [previous]

        previous = step.id

    return wf


def initialise_step_runtime(run: WorkflowRun, wf: WorkflowDefinition) -> None:
    for step in wf.steps:
        run.steps.setdefault(step.id, StepRuntime())


def validate_dag(wf: WorkflowDefinition) -> None:
    step_ids = {step.id for step in wf.steps}

    if len(step_ids) != len(wf.steps):
        raise ValueError("Workflow DAG contains duplicate step ids.")

    by_id = _steps_by_id(wf)

    for step in wf.steps:
        for dep in step.depends_on:
            if dep not in step_ids:
                raise ValueError(f"Step '{step.id}' depends on unknown step '{dep}'")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(step_id: str) -> None:
        if step_id in visited:
            return

        if step_id in visiting:
            raise ValueError(f"Workflow DAG contains a cycle at step '{step_id}'")

        visiting.add(step_id)

        for dep in by_id[step_id].depends_on:
            visit(dep)

        visiting.remove(step_id)
        visited.add(step_id)

    for step_id in step_ids:
        visit(step_id)


def dependencies_completed(step: WorkflowStep, run: WorkflowRun) -> bool:
    for dep in step.depends_on:
        runtime = run.steps.get(dep)

        if runtime is None:
            return False

        if runtime.status != "COMPLETED":
            return False

    return True


def ready_steps(wf: WorkflowDefinition, run: WorkflowRun) -> list[WorkflowStep]:
    ready: list[WorkflowStep] = []

    for step in wf.steps:
        runtime = run.steps.get(step.id)

        if runtime is None:
            continue

        if runtime.status != "PENDING":
            continue

        if dependencies_completed(step, run):
            ready.append(step)

    return ready


def all_steps_terminal(wf: WorkflowDefinition, run: WorkflowRun) -> bool:
    terminal = {"CANCELLED", "COMPLETED", "FAILED", "SKIPPED"}

    for step in wf.steps:
        runtime = run.steps.get(step.id)

        if runtime is None:
            return False

        if runtime.status not in terminal:
            return False

    return True


def any_step_failed(wf: WorkflowDefinition, run: WorkflowRun) -> bool:
    for step in wf.steps:
        runtime = run.steps.get(step.id)

        if runtime is not None and runtime.status == "FAILED":
            return True

    return False


def _steps_by_id(wf: WorkflowDefinition) -> dict[str, WorkflowStep]:
    return {step.id: step for step in wf.steps}
