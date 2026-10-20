from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal, NamedTuple

from pydantic import BaseModel, Field, field_validator, model_validator

from src.domains.agentverse.workflows.spec import WorkflowInputField

ExecutionMode = Literal["sequential", "dag"]
RunStatus = Literal[
    "RUNNING",
    "PAUSED",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
]
StepStatus = Literal[
    "PENDING",
    "RUNNING",
    "PAUSED",
    "COMPLETED",
    "FAILED",
    "SKIPPED",
    "CANCELLED",
]
StepType = Literal["tool", "pause", "set", "wait_event", "end"]


def now_utc_iso() -> str:
    return datetime.now(UTC).isoformat()


DEFAULT_PAUSE_PROMPT = "Paused — continue when ready."


class PauseSpec(BaseModel):
    prompt: str = DEFAULT_PAUSE_PROMPT


class EventWaitSpec(BaseModel):
    event_type: str
    correlation_id: str
    timeout_seconds: int | None = None

    @field_validator("event_type", "correlation_id")
    @classmethod
    def non_empty_string(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value

    @field_validator("timeout_seconds")
    @classmethod
    def valid_timeout(cls, value: int | None) -> int | None:
        if value is not None and value <= 0:
            raise ValueError("timeout_seconds must be greater than 0")
        return value


class RetryPolicy(BaseModel):
    attempts: int = 1
    backoff: Literal["fixed", "exponential"] = "fixed"
    delay_seconds: float = 0.0

    @field_validator("attempts")
    @classmethod
    def valid_attempts(cls, value: int) -> int:
        if value < 1:
            raise ValueError("attempts must be at least 1")
        return value

    @field_validator("delay_seconds")
    @classmethod
    def valid_delay(cls, value: float) -> float:
        if value < 0:
            raise ValueError("delay_seconds must be >= 0")
        return value


class WorkflowStep(BaseModel):
    id: str
    type: StepType = "tool"

    # Human-readable step name for the canvas/builder. Falls back to `id` in the
    # frontend when absent.
    label: str | None = None

    command: str | None = None

    depends_on: list[str] = Field(default_factory=list)

    # Slot name -> single ${...} reference to an upstream image output or a
    # run file input (`${inputs.<key>}`). A parallel channel to the text
    # command: the engine resolves each reference and injects the file(s) into
    # the consuming tool's `files` kwargs.
    file_bindings: dict[str, str] = Field(default_factory=dict)

    pause: PauseSpec | None = None
    set: dict[str, Any] | None = None
    wait_for: EventWaitSpec | None = None
    retry: RetryPolicy | dict[str, Any] | None = None

    @field_validator("id")
    @classmethod
    def valid_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("step id must not be empty")
        return value

    @field_validator("label")
    @classmethod
    def clean_label(cls, value: str | None) -> str | None:
        """A whitespace-only label is no label - fall back to `id` rather than
        shipping blank text to the canvas."""
        if value is None:
            return None
        return value.strip() or None

    @field_validator("depends_on")
    @classmethod
    def unique_step_refs(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []

        for item in value:
            item = item.strip()
            if not item:
                raise ValueError("step references must not be empty")
            if item not in cleaned:
                cleaned.append(item)

        return cleaned

    @field_validator("file_bindings")
    @classmethod
    def valid_file_bindings(cls, value: dict[str, str]) -> dict[str, str]:
        cleaned: dict[str, str] = {}

        for slot, ref in value.items():
            slot = slot.strip()
            if not slot:
                raise ValueError("file binding slot names must not be empty")
            if slot in cleaned:
                raise ValueError(
                    f"file binding slot '{slot}' defined multiple times (after stripping)"
                )
            cleaned[slot] = ref

        return cleaned

    @model_validator(mode="after")
    def validate_step_payload(self) -> WorkflowStep:
        if self.type == "tool" and not self.command:
            raise ValueError(f"tool step '{self.id}' requires command")

        if self.file_bindings and self.type != "tool":
            raise ValueError(f"step '{self.id}': file_bindings only apply to tool steps")

        if self.type == "pause" and self.pause is None:
            # Fall back to a default pause prompt
            self.pause = PauseSpec()

        if self.type == "set" and self.set is None:
            raise ValueError(f"set step '{self.id}' requires set payload")

        if self.type == "wait_event" and self.wait_for is None:
            raise ValueError(f"wait_event step '{self.id}' requires wait_for spec")

        return self


class WorkflowDefinition(BaseModel):
    steps: list[WorkflowStep]
    execution_mode: ExecutionMode = "sequential"

    @field_validator("steps")
    @classmethod
    def non_empty_steps(cls, value: list[WorkflowStep]) -> list[WorkflowStep]:
        if not value:
            raise ValueError("workflow must contain at least one step")
        return value

    @model_validator(mode="after")
    def unique_step_ids(self) -> WorkflowDefinition:
        ids = [step.id for step in self.steps]

        if len(ids) != len(set(ids)):
            raise ValueError("workflow contains duplicate step ids")

        return self


class ResolvedWorkflow(NamedTuple):
    """An executable workflow resolved from the catalog or a run snapshot: its
    definition, display ``name`` (``None`` when unavailable), and declared run
    ``inputs`` (``[]`` on the snapshot path, which never needs them - file
    inputs are materialized at start)."""

    definition: WorkflowDefinition
    name: str | None
    inputs: list[WorkflowInputField]


class StepRuntime(BaseModel):
    status: StepStatus = "PENDING"
    attempts: int = 0
    started_at: str | None = None
    completed_at: str | None = None
    last_error: str | None = None


@dataclass(slots=True)
class StepExecutionResult:
    step_id: str
    ok: bool
    status: str
    output: Any | None = None
    state_patch: dict[str, Any] = field(default_factory=dict)
    pause: PauseState | None = None
    wait_for: EventWaitSpec | None = None
    event_index: dict[str, str] | None = None
    error: str | None = None


class PauseState(BaseModel):
    pause_id: str
    step_id: str
    prompt: str
    workflow_key: str | None = None
    workflow_name: str | None = None
    actions: list[str] = Field(default_factory=list)
    on_continue: str | None = None
    on_cancel: str | None = None
    kind: str | None = None
    # Internal-only: nested-workflow resume plumbing. Stripped from the
    # frontend snapshot in `first_pause`; never part of the run contract.
    nested_workflow: dict[str, Any] | None = None


class WorkflowRun(BaseModel):
    run_id: str
    workflow_name: str | None = None
    created_at: str
    updated_at: str

    status: RunStatus = "RUNNING"

    inputs: dict[str, Any] = Field(default_factory=dict)
    state: dict[str, Any] = Field(default_factory=dict)

    steps: dict[str, StepRuntime] = Field(default_factory=dict)
    paused: dict[str, PauseState] = Field(default_factory=dict)
    waiting_events: dict[str, EventWaitSpec] = Field(default_factory=dict)

    # Snapshot of the resolved WorkflowDefinition captured at run start.
    # Resume rebuilds the workflow from this instead of re-resolving by key, so
    # in-flight edits to the source row cannot corrupt a paused run. ``None`` for
    # legacy runs created before snapshot-on-run (resume falls back to re-resolve).
    definition: dict[str, Any] | None = None

    last_error: str | None = None
