"""
Persistence + request entities for the workflow catalog (``coll_workflows``).

A workflow document is the :class:`WorkflowSpec` shape (key/name/definition/...)
plus persistence + audit metadata. ``key`` is the immutable foreign reference
held by ``Agent.workflows``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from src.domains.agentverse.workflows.spec import WorkflowInputField

if TYPE_CHECKING:
    from src.domains.agentverse.workflows.spec import WorkflowSpec

WorkflowOrigin = Literal["builtin", "db"]


def slugify_workflow_key(key: str | None) -> str:
    """Canonical workflow-key slug. The one place create + seed agree on so a
    builtin's seeded key can never diverge from a re-created one."""
    return (key or "").strip().lower().replace(" ", "_")


class WorkflowDocument(BaseModel):
    """A persisted workflow row. Mirrors ``WorkflowSpec`` + audit metadata."""

    key: str  # Immutable, unique-indexed. The FK held by agents.
    name: str
    description: str | None = None
    version: str = "0.1.0"

    definition: dict[str, Any]  # WorkflowDefinition-shaped engine payload.

    required_tools: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    inputs: list[WorkflowInputField] = Field(default_factory=list)
    ui: dict[str, Any] = Field(default_factory=dict)

    origin: WorkflowOrigin = "db"

    created_by: str | None = None
    updated_by: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_spec(
        cls,
        spec: WorkflowSpec,
        *,
        origin: WorkflowOrigin,
        now: str,
        required_tools: list[str],
    ) -> WorkflowDocument:
        """Build a persisted row from a code-defined ``WorkflowSpec`` (the seed path).

        ``required_tools`` is passed in rather than read off the spec: it is derived
        from the definition's steps, so the seed path and the REST path store the
        same thing and a spec cannot declare tools its DAG never invokes.
        """
        return cls(
            key=slugify_workflow_key(spec.key),
            name=spec.name,
            description=spec.description,
            version=spec.version,
            definition=spec.definition,
            required_tools=required_tools,
            tags=spec.tags,
            inputs=spec.inputs,
            ui=spec.ui,
            origin=origin,
            created_at=now,
            updated_at=now,
        )


class WorkflowCreateRequest(BaseModel):
    """Payload accepted by ``POST /api/v1/workflows``."""

    name: str
    # Optional explicit key; otherwise slugified from ``name`` at create time.
    key: str | None = None
    description: str | None = None
    version: str = "0.1.0"

    definition: dict[str, Any]

    # No ``required_tools``: it is derived from the definition's steps at save
    # time (see ``WorkflowService._assert_valid``). Accepting a declared list
    # would let a caller equip attached agents with tools the DAG never invokes.
    tags: list[str] = Field(default_factory=list)
    inputs: list[WorkflowInputField] = Field(default_factory=list)
    ui: dict[str, Any] = Field(default_factory=dict)


class WorkflowUpdateRequest(BaseModel):
    """
    Payload accepted by ``PATCH /api/v1/workflows/key/{key}``.

    ``key``/``origin``/``created_*`` are immutable and intentionally absent.
    Only fields explicitly provided are applied.
    """

    name: str | None = None
    description: str | None = None
    version: str | None = None

    definition: dict[str, Any] | None = None

    tags: list[str] | None = None
    inputs: list[WorkflowInputField] | None = None
    ui: dict[str, Any] | None = None

    def model_dump_for_update(self) -> dict[str, Any]:
        """Only the fields the caller actually set (drops unset/None).

        Note: because ``None`` is dropped, a nullable field (e.g. ``description``)
        cannot be *cleared* back to null through this path — only overwritten with
        a new non-null value.
        """
        return self.model_dump(exclude_unset=True, exclude_none=True)


class WorkflowValidationIssue(BaseModel):
    """One structured validation error for the canvas to highlight."""

    code: str
    message: str
    step_id: str | None = None
    tool_key: str | None = None


class WorkflowValidationResult(BaseModel):
    """Result of a dry-run validation (``POST /api/v1/workflows/validate``)."""

    valid: bool
    errors: list[WorkflowValidationIssue] = Field(default_factory=list)
    # Advisory issues (e.g. NON_FILE_INPUT_IN_SLOT) — surfaced by the builder,
    # never block a save. ``valid`` is unaffected.
    warnings: list[WorkflowValidationIssue] = Field(default_factory=list)
    # Canonical tool keys the steps invoke, resolved during validation. Carried on
    # the result so persistence can store them without re-parsing the definition.
    # Empty whenever ``valid`` is False — validation bails before resolving them.
    required_tools: list[str] = Field(default_factory=list)
