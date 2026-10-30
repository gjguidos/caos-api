"""Genesis provisioning contracts. Results describe outcomes; they grant nothing."""

from __future__ import annotations

from typing import Annotated, Any, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

_ID = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"


class ApprovedCreation(BaseModel):
    """The effective human approval a governed manual creation writes (G3.4). Store
    independently re-verifies it; naming it here confers nothing."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    approval_id: Annotated[str, Field(pattern=_ID)]
    review_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    creation_id: Annotated[str, Field(pattern=_ID)]


class GenesisProvisionRequest(BaseModel):
    """What a trusted caller asks Genesis to provision. Identity and authority come
    from the CPBX-issued client, never from this request. Exactly one target: a
    policy-pinned ``slot`` or an ``approved`` human-reviewed creation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    slot: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")] | None = None
    approved: ApprovedCreation | None = None
    creation: dict[str, Any]  # Agent Creation Input, validated by data.validation

    @model_validator(mode="after")
    def _one_target(self) -> Self:
        if (self.slot is None) == (self.approved is None):
            raise ValueError("Exactly one of slot or approved is required")
        return self

    @property
    def step(self) -> str:
        """Stable audit step of this target (unchanged for slots)."""
        return self.slot if self.slot is not None else f"approved:{self.approved.approval_id}"


GenesisStatus = Literal["provisioned", "rejected", "failed"]
GenesisStage = Literal["validation", "composition", "store"]


class GenesisProvisionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation_id: UUID
    correlation_id: str
    status: GenesisStatus
    stage: GenesisStage | None = None  # where a rejection/failure stopped execution
    agent_id: str | None = None
    created: bool | None = None  # Store wrote the definition in this attempt
    retryable: bool = False  # the operation is retry-pending; repeat the same request
    replayed: bool = False  # returned from a completed operation, nothing re-executed
    issue_codes: tuple[str, ...] = ()
    audit_event_ids: tuple[str, ...] = ()


class GenesisError(Exception):
    """Genesis could not reach a confirmed, audited outcome."""


class GenesisAuditError(GenesisError):
    """Seshat could not record required evidence. Effects that already happened are not
    undone; the operation is left retry-pending (or failed for conflicts), never succeeded."""

    def __init__(self, operation_id: UUID, cause: Exception, *, retryable: bool) -> None:
        super().__init__(f"Genesis audit evidence could not be recorded for {operation_id}")
        self.operation_id = operation_id
        self.retryable = retryable
        self.__cause__ = cause


class GenesisOperationInProgress(GenesisError):
    """Another attempt holds execution authority, or another caller won the race to
    reconcile an interrupted attempt. The operation is reported, never re-executed
    blindly; repeat the identical request once that authority is released or expires.
    """

    def __init__(self, operation_id: UUID, *, interrupted: bool) -> None:
        state = "interrupted and requires reconciliation" if interrupted else "in progress"
        super().__init__(f"Genesis operation {operation_id} is {state}")
        self.operation_id = operation_id
        self.interrupted = interrupted


class GenesisAuditMissing(GenesisError):
    """A completed operation has no matching Seshat evidence (integrity failure)."""
