from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

FILE_INPUT_KIND = "file"


class WorkflowInputField(BaseModel):
    """Describes one declared input for a workflow, using the same field-config
    vocabulary as tool ui.admin.fields so the frontend's DynamicFormRenderer can
    consume it directly."""

    model_config = ConfigDict(extra="allow")

    key: str
    label: str
    input: str = "text"  # text | textarea | number | select | multiselect | object | file
    required: bool = False
    help: str | None = None
    default: Any | None = None
    constraints: dict[str, Any] | None = None
    options: list[dict[str, Any]] | None = None
    source: dict[str, Any] | None = None

    @property
    def is_file(self) -> bool:
        return self.input == FILE_INPUT_KIND

    @property
    def accepted_mimes(self) -> list[str]:
        """Declared ``constraints.accepted_mimes`` (empty = any file). Values may
        include ``type/*`` wildcards."""
        raw = (self.constraints or {}).get("accepted_mimes") or []
        return [m for m in raw if isinstance(m, str) and m.strip()]

    @model_validator(mode="after")
    def validate_file_constraints(self) -> WorkflowInputField:
        if not self.is_file:
            return self

        raw = (self.constraints or {}).get("accepted_mimes")
        if raw is not None and (
            not isinstance(raw, list) or any(not isinstance(m, str) for m in raw)
        ):
            raise ValueError(
                f"file input '{self.key}': constraints.accepted_mimes must be a list of strings"
            )

        return self


class WorkflowSpec(BaseModel):
    key: str
    name: str
    description: str | None = None
    version: str = "0.1.0"

    # The engine consumes this (validated into WorkflowDefinition at runtime)
    definition: dict[str, Any]

    # No ``required_tools``: a workflow's tool set is derived from its steps at
    # save time (``validate_workflow_definition``), so there is nothing to declare.
    tags: list[str] = Field(default_factory=list)

    # Declared run inputs — rendered by the frontend's dynamic form renderer.
    # Optional; workflows with no inputs simply omit this or leave it empty.
    inputs: list[WorkflowInputField] = Field(default_factory=list)

    # Optional UI metadata (icon, category, etc.)
    ui: dict[str, Any] = Field(default_factory=dict)
