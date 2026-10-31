"""Human routes for governed Agent revision authoring and review (REV-2, ADR-0005).

Authenticated Genesis human sessions only, with the existing ``agent.write`` route policy.
No route here approves, applies or activates anything, and no Agent, System Citizen or
generic signal route can reach these operations. Paths have three or more segments so the
generic ``/{agent_id}/{operation}`` lifecycle route never captures them.
"""

from collections.abc import Awaitable, Callable
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, TypeAdapter, ValidationError

from src.base.authentication.auth_core import check_roles_and_scopes
from src.base.authentication.permission import Permission
from src.base.authentication.rbac import _check_permissions
from src.base.authentication.role import Role
from src.base.entities.user import User
from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.agents.definition_revisions.application import (
    RevisionApplicationError,
    RevisionApplicationNotFound,
)
from src.domains.agentverse.agents.definition_revisions.approval import (
    RevisionApprovalError,
    RevisionApprovalNotFound,
    RevisionApprovalRequest,
    RevisionWithdrawalRequest,
)
from src.domains.agentverse.agents.definition_revisions.drafts import (
    IdempotencyKey,
    RevisionDraftConflict,
    RevisionDraftCreateRequest,
    RevisionDraftError,
    RevisionDraftNotFound,
    RevisionDraftUpdateRequest,
)
from src.domains.agentverse.agents.definition_revisions.models import RevisionError
from src.domains.agentverse.agents.definition_revisions.review import (
    RevisionReviewError,
    RevisionReviewNotFound,
)
from src.domains.agentverse.agents.definition_revisions.service import (
    RevisionBaselineUnavailable,
    RevisionTargetNotFound,
)
from src.domains.agentverse.interfaces.api.v2.genesis_sessions import (
    _token,
    authenticated_genesis,
)

router = APIRouter(prefix="/api/v2/agentverse/agents", tags=["Agent Definition Revisions"])
_IDEMPOTENCY_KEY: TypeAdapter[str] = TypeAdapter(IdempotencyKey)


class RevisionReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    contract: Literal["caos.agent_definition_revision_review_request"]
    schema_version: StrictInt = Field(ge=1, le=1)
    expected_revision: StrictInt = Field(ge=1)


def _approvals(request: Request) -> Any:
    service = getattr(request.app.state, "agent_definition_revision_approvals", None)
    if service is None:
        raise EventPermissionError("Agent revision approvals are not configured")
    return service


def _service(request: Request) -> Any:
    service = getattr(request.app.state, "agent_definition_revisions", None)
    if service is None:
        raise EventPermissionError("Agent revisions are not configured")
    return service


def _reviser_policy(request: Request, token: str) -> Callable[[User], Awaitable[None]]:
    async def authorize(requester: User) -> None:
        # The existing agent.write route policy, from the same verified bearer.
        if not check_roles_and_scopes(requester.roles, requester.scopes, [[Role.USER]]):
            raise EventPermissionError("Agent revision requires a user")
        await _check_permissions(
            request, requester.model_copy(update={"access_token": token}), [Permission.AGENT_WRITE]
        )

    return authorize


async def _governed(request: Request, operation: Callable[[Any, Any], Awaitable[Any]]) -> Any:
    """One operation in the verified session; typed failures map to stable codes."""
    try:
        token = _token(request)
        async with authenticated_genesis(request) as session:
            return await operation(session, _reviser_policy(request, token))
    except (
        RevisionTargetNotFound,
        RevisionDraftNotFound,
        RevisionReviewNotFound,
        RevisionApprovalNotFound,
        RevisionApplicationNotFound,
    ) as exc:
        raise HTTPException(404, {"code": "revision.not_found", "retryable": False}) from exc
    except EventPermissionError as exc:
        raise HTTPException(403, "Agent revision denied") from exc
    except RevisionDraftConflict as exc:
        raise HTTPException(
            409, {"code": exc.code, "retryable": False, "current_revision": exc.current_revision}
        ) from exc
    except RevisionBaselineUnavailable as exc:
        raise HTTPException(409, {"code": exc.code, "retryable": False}) from exc
    except (
        RevisionDraftError,
        RevisionReviewError,
        RevisionError,
        RevisionApprovalError,
        RevisionApplicationError,
    ) as exc:
        status = 503 if exc.retryable else (500 if exc.code.endswith("corrupt") else 409)
        raise HTTPException(status, {"code": exc.code, "retryable": exc.retryable}) from exc
    except KeyError as exc:
        raise HTTPException(503, "Exact validation provider is unavailable") from exc


@router.get("/{agent_id}/definition-revisions/baseline")
async def get_definition_baseline(request: Request, agent_id: str) -> Any:
    """Read-only: whether the requester's own Agent can be revised, and from what."""

    async def run(session: Any, policy: Any) -> Any:
        return await _service(request).baseline(session, agent_id, policy=policy)

    return await _governed(request, run)


@router.post("/{agent_id}/definition-revisions/drafts")
async def create_revision_draft(
    request: Request,
    agent_id: str,
    payload: RevisionDraftCreateRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> Any:
    """Start the requester's revision draft (draft revision 1). Changes no Agent."""
    try:
        key = _IDEMPOTENCY_KEY.validate_python(idempotency_key)
    except ValidationError as exc:
        raise HTTPException(422, "Invalid Idempotency-Key") from exc

    async def run(session: Any, policy: Any) -> Any:
        draft, created = await _service(request).create_draft(
            session, agent_id, payload, key, policy=policy
        )
        return JSONResponse(draft.public(), status_code=201 if created else 200)

    return await _governed(request, run)


@router.get("/{agent_id}/definition-revisions/drafts/{draft_id}")
async def get_revision_draft(request: Request, agent_id: str, draft_id: UUID) -> Any:
    async def run(session: Any, policy: Any) -> Any:
        draft = await _service(request).get_draft(session, agent_id, str(draft_id), policy=policy)
        return draft.public()

    return await _governed(request, run)


@router.patch("/{agent_id}/definition-revisions/drafts/{draft_id}")
async def update_revision_draft(
    request: Request, agent_id: str, draft_id: UUID, payload: RevisionDraftUpdateRequest
) -> Any:
    """Replace the complete candidate against the edited draft revision (CAS)."""

    async def run(session: Any, policy: Any) -> Any:
        draft = await _service(request).update_draft(
            session, agent_id, str(draft_id), payload, policy=policy
        )
        return draft.public()

    return await _governed(request, run)


@router.post("/{agent_id}/definition-revisions/drafts/{draft_id}/reviews")
async def review_revision_draft(
    request: Request, agent_id: str, draft_id: UUID, payload: RevisionReviewRequest
) -> Any:
    """Validate and review the exact draft revision. Never approves or applies."""

    async def run(session: Any, policy: Any) -> Any:
        review = await _service(request).review(
            session, agent_id, str(draft_id), payload.expected_revision, policy=policy
        )
        return JSONResponse(
            {**review.envelope, "state": "current" if review.eligible else "ineligible"},
            status_code=201,
        )

    return await _governed(request, run)


@router.get("/{agent_id}/definition-revisions/reviews/{review_id}")
async def get_revision_review(request: Request, agent_id: str, review_id: UUID) -> Any:
    """The stored review and whether it is still current (fails closed when stale)."""

    async def run(session: Any, policy: Any) -> Any:
        return await _service(request).review_status(
            session, agent_id, str(review_id), policy=policy
        )

    return await _governed(request, run)


@router.post("/{agent_id}/definition-revisions/approvals")
async def approve_revision(
    request: Request, agent_id: str, payload: RevisionApprovalRequest
) -> Any:
    """Explicit human approval of one exact, current review. Never applies a revision."""

    async def run(session: Any, policy: Any) -> Any:
        service = _approvals(request)
        record = await service.approve(session, agent_id, payload, policy=policy)
        return JSONResponse(record.public(service.now()), status_code=201)

    return await _governed(request, run)


@router.get("/{agent_id}/definition-revisions/approvals/{approval_id}")
async def get_revision_approval(request: Request, agent_id: str, approval_id: UUID) -> Any:
    """The human-safe decision and whether it is currently effective."""

    async def run(session: Any, policy: Any) -> Any:
        return await _approvals(request).status(session, agent_id, str(approval_id), policy=policy)

    return await _governed(request, run)


@router.post("/{agent_id}/definition-revisions/approvals/{approval_id}/withdrawal")
async def withdraw_revision_approval(
    request: Request, agent_id: str, approval_id: UUID, payload: RevisionWithdrawalRequest
) -> Any:
    """The approving human withdraws a pending or approved, unapplied decision."""

    async def run(session: Any, policy: Any) -> Any:
        service = _approvals(request)
        record = await service.withdraw(session, agent_id, str(approval_id), payload, policy=policy)
        return record.public(service.now())

    return await _governed(request, run)


def _applications(request: Request) -> Any:
    service = getattr(request.app.state, "agent_definition_revision_applications", None)
    if service is None:
        raise EventPermissionError("Agent revision application is not configured")
    return service


@router.post("/{agent_id}/definition-revisions/approvals/{approval_id}/application")
async def apply_revision(request: Request, agent_id: str, approval_id: UUID) -> Any:
    """Claim and apply the owner's effective approval through Genesis (REV-4).

    The human never invokes the Store signal: Genesis executes it under the exact
    ``agent.definition.revise`` scope. Runtime reconciliation is not performed.
    """

    async def run(session: Any, policy: Any) -> Any:
        record = await _applications(request).apply(
            session, agent_id, str(approval_id), policy=policy
        )
        return JSONResponse(
            record.public(), status_code=200 if record.state == "completed" else 202
        )

    return await _governed(request, run)


@router.get("/{agent_id}/definition-revisions/approvals/{approval_id}/application")
async def get_revision_application(request: Request, agent_id: str, approval_id: UUID) -> Any:
    async def run(session: Any, policy: Any) -> Any:
        record = await _applications(request).status(
            session, agent_id, str(approval_id), policy=policy
        )
        return record.public()

    return await _governed(request, run)
