"""Session-bound V2 lifecycle adapters; no caller-written identity or grants."""

from collections.abc import Awaitable, Callable
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Path, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt, TypeAdapter, ValidationError

from src.base.authentication.auth_core import check_roles_and_scopes
from src.base.authentication.permission import Permission
from src.base.authentication.rbac import _check_permissions
from src.base.authentication.role import Role
from src.base.entities.user import User
from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.agents.creation_drafts.lifecycle import (
    DraftApprovalRequest,
    DraftReopenRequest,
)
from src.domains.agentverse.agents.creation_drafts.models import (
    AgentDraftCreateRequest,
    AgentDraftForkRequest,
    AgentDraftUpdateRequest,
    CorruptDraft,
    DraftError,
    DraftInvalidRequest,
    DraftLocked,
    DraftNotFound,
    RevisionConflict,
)
from src.domains.agentverse.agents.creation_drafts.proposals import (
    CorruptProposal,
    ProposalDecisionRequest,
    ProposalFieldConflict,
    ProposalInvalid,
    ProposalNotFound,
)
from src.domains.agentverse.agents.creation_review.creation import AgentCreationRequest
from src.domains.agentverse.agents.creation_review.models import (
    ApprovalError,
    IdempotencyKey,
    ReviewApprovalRequest,
)
from src.domains.agentverse.interfaces.api.v2.genesis_sessions import (
    _token,
    authenticated_genesis,
)
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey
from src.domains.agentverse.security import agent_access
from src.domains.agentverse.security.validation_gateway import AgentDraftValidationRequest

router = APIRouter(prefix="/api/v2/agentverse/agents", tags=["Genesis Agents"])
_IDEMPOTENCY_KEY: TypeAdapter[str] = TypeAdapter(IdempotencyKey)


@router.post("/{source_agent_id}/creation-draft/validate")
async def validate_creation_draft(
    request: Request, source_agent_id: str, payload: AgentDraftValidationRequest
) -> Any:
    try:
        async with authenticated_genesis(request) as session:
            gateway = getattr(request.app.state, "agent_draft_validation_gateway", None)
            if gateway is None:
                raise EventPermissionError("Validation gateway is not configured")
            return await gateway.validate(session, source_agent_id, payload)
    except EventPermissionError as exc:
        raise HTTPException(403, "Draft validation denied") from exc
    except KeyError as exc:
        raise HTTPException(503, "Exact validation provider is unavailable") from exc


def _approvals(request: Request) -> Any:
    service = getattr(request.app.state, "agent_draft_review_approvals", None)
    if service is None:
        raise EventPermissionError("Draft review approval is not configured")
    return service


def _approval_error(exc: ApprovalError) -> HTTPException:
    if exc.retryable:
        return HTTPException(503, {"code": exc.code, "retryable": True})
    status = 404 if exc.code == "approval.not_found" else 409
    return HTTPException(status, {"code": exc.code, "retryable": False})


def _creator_policy(request: Request, token: str) -> Any:
    async def authorize_creator(requester: User) -> None:
        # The existing agent.write route policy and creation rule, applied to the
        # trusted requester resolved from the same verified bearer.
        if not check_roles_and_scopes(
            requester.roles, requester.scopes, [[Role.USER]]
        ) or not agent_access.can_create(requester):
            raise EventPermissionError("Agent creation requires a creator")
        await _check_permissions(
            request, requester.model_copy(update={"access_token": token}), [Permission.AGENT_WRITE]
        )

    return authorize_creator


class AgentActivationRequest(BaseModel):
    """Reference to the Genesis operation that created the agent; never authority."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    contract: Literal["caos.agent_activation_request"]
    schema_version: StrictInt = Field(ge=1, le=1)
    creation_operation_id: UUID = Field(strict=False)  # JSON string, validated UUID


@router.post("/{agent_id}/activation")
async def activate_provisioned_agent(
    request: Request, agent_id: str, payload: AgentActivationRequest
) -> Any:
    """Separately authorized Khepri activation of a Genesis-provisioned agent."""
    try:
        async with authenticated_genesis(request) as session:
            activation = getattr(request.app.state, "genesis_activation", None)
            if activation is None:
                raise EventPermissionError("Genesis activation is not configured")
            result = await activation.activate_provisioned(
                session, agent_id, str(payload.creation_operation_id)
            )
            return {"agent_id": agent_id, **result}
    except EventPermissionError as exc:
        raise HTTPException(403, "Agent activation denied") from exc


@router.post("/{source_agent_id}/creation-draft/create")
async def create_approved_agent(
    request: Request,
    source_agent_id: str,
    payload: AgentCreationRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> Any:
    """Create one effective-approved Agent through Genesis, sleeping. Never activates."""
    try:
        token = _token(request)
        async with authenticated_genesis(request) as session:
            service = getattr(request.app.state, "agent_creation", None)
            if service is None:
                raise EventPermissionError("Governed creation is not configured")
            record = await service.create(
                session,
                source_agent_id,
                payload,
                idempotency_key,
                authorize_creator=_creator_policy(request, token),
            )
            final = record.state in ("completed", "failed")
            return JSONResponse(record.public(), status_code=200 if final else 202)
    except EventPermissionError as exc:
        raise HTTPException(403, "Agent creation denied") from exc
    except ApprovalError as exc:
        raise _approval_error(exc) from exc
    except ValidationError as exc:
        raise HTTPException(422, "Invalid Idempotency-Key") from exc
    except KeyError as exc:
        raise HTTPException(503, "Exact Genesis provider is unavailable") from exc


@router.post("/{source_agent_id}/creation-draft/approvals")
async def approve_creation_draft(
    request: Request, source_agent_id: str, payload: ReviewApprovalRequest
) -> Any:
    """Explicit human approval of one exact reviewed draft; never creation authority."""
    try:
        token = _token(request)
        authorize_creator = _creator_policy(request, token)
        async with authenticated_genesis(request) as session:
            approval = await _approvals(request).approve(
                session, source_agent_id, payload, authorize_creator=authorize_creator
            )
            return approval.public(_approvals(request).now())
    except EventPermissionError as exc:
        raise HTTPException(403, "Draft review approval denied") from exc
    except ApprovalError as exc:
        raise _approval_error(exc) from exc
    except KeyError as exc:
        raise HTTPException(503, "Exact validation provider is unavailable") from exc


@router.post("/{source_agent_id}/creation-draft/approvals/{approval_id}/revoke")
async def revoke_creation_draft_approval(
    request: Request, source_agent_id: str, approval_id: str
) -> Any:
    try:
        async with authenticated_genesis(request) as session:
            approvals = _approvals(request)
            approval = await approvals.revoke(session, source_agent_id, approval_id)
            return approval.public(approvals.now())
    except EventPermissionError as exc:
        raise HTTPException(403, "Draft review approval denied") from exc
    except ApprovalError as exc:
        raise _approval_error(exc) from exc


def _drafts(request: Request) -> Any:
    service = getattr(request.app.state, "agent_drafts", None)
    if service is None:
        raise EventPermissionError("Agent drafts are not configured")
    return service


def _draft_error(exc: DraftError) -> HTTPException:
    if exc.retryable:
        return HTTPException(503, {"code": exc.code, "retryable": True})
    if isinstance(exc, RevisionConflict):
        return HTTPException(
            409, {"code": exc.code, "retryable": False, "current_revision": exc.current_revision}
        )
    if isinstance(exc, ProposalFieldConflict):
        return HTTPException(
            409,
            {
                "code": exc.code,
                "retryable": False,
                "current_revision": exc.current_revision,
                "conflicting_fields": exc.conflicting,
                "applicable_fields": exc.applicable,
            },
        )
    if isinstance(exc, DraftLocked):
        return HTTPException(
            409, {"code": exc.code, "retryable": False, "current_revision": exc.current_revision}
        )
    if isinstance(exc, DraftNotFound | ProposalNotFound):
        status = 404
    elif isinstance(exc, ProposalInvalid | DraftInvalidRequest):
        status = 422
    elif isinstance(exc, CorruptDraft | CorruptProposal):
        status = 500
    else:  # Idempotency, already decided, decision in progress, expired.
        status = 409
    return HTTPException(status, {"code": exc.code, "retryable": False})


# Declared before the generic ``/{agent_id}/{operation}`` lifecycle route, which would
# otherwise capture ``POST /{source_agent_id}/creation-drafts``.
@router.post("/{source_agent_id}/creation-drafts")
async def create_creation_draft(
    request: Request,
    source_agent_id: str,
    payload: AgentDraftCreateRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> Any:
    """Create the requester's durable draft (revision 1). Never validates or creates."""
    try:
        key = _IDEMPOTENCY_KEY.validate_python(idempotency_key)
        token = _token(request)
        async with authenticated_genesis(request) as session:
            record, created = await _drafts(request).create(
                session,
                source_agent_id,
                payload,
                key,
                authorize_creator=_creator_policy(request, token),
            )
            return JSONResponse(record.public(), status_code=201 if created else 200)
    except EventPermissionError as exc:
        raise HTTPException(403, "Agent draft denied") from exc
    except DraftError as exc:
        raise _draft_error(exc) from exc
    except ValidationError as exc:
        raise HTTPException(422, "Invalid Idempotency-Key") from exc


def _draft_lifecycle(request: Request) -> Any:
    service = getattr(request.app.state, "agent_draft_lifecycle", None)
    if service is None:
        raise EventPermissionError("Agent draft lifecycle is not configured")
    return service


async def _governed(request: Request, operation: Callable[[Any, Any], Awaitable[Any]]) -> Any:
    """Run one draft operation inside the verified Genesis session with the existing
    creator policy; typed failures map to stable codes, never upstream text."""
    try:
        token = _token(request)
        async with authenticated_genesis(request) as session:
            return await operation(session, _creator_policy(request, token))
    except EventPermissionError as exc:
        raise HTTPException(403, "Agent draft denied") from exc
    except DraftError as exc:
        raise _draft_error(exc) from exc
    except ApprovalError as exc:
        raise _approval_error(exc) from exc
    except KeyError as exc:
        raise HTTPException(503, "Exact validation provider is unavailable") from exc


@router.get("/{source_agent_id}/creation-drafts")
async def list_creation_drafts(
    request: Request,
    source_agent_id: str,
    status: str = "open",
    limit: int = 20,
    cursor: str | None = None,
) -> Any:
    """The requester's own drafts for this source, newest first (``open`` = resumable)."""

    async def run(session: Any, authorize: Any) -> Any:
        lifecycle = _draft_lifecycle(request)
        items, following = await _drafts(request).list_drafts(
            session,
            source_agent_id,
            listing=status,
            limit=limit,
            cursor=cursor,
            authorize_creator=authorize,
            reconcile=lifecycle.reconcile,
        )
        return {
            "contract": "caos.agent_draft_page",
            "schema_version": 1,
            "items": [item.public() for item in items],
            "next_cursor": following,
        }

    return await _governed(request, run)


@router.get("/{source_agent_id}/creation-drafts/{draft_id}")
async def get_creation_draft(request: Request, source_agent_id: str, draft_id: UUID) -> Any:
    """The current draft; a G3.4-confirmed creation is recorded as consumption first."""

    async def run(session: Any, authorize: Any) -> Any:
        record = await _draft_lifecycle(request).get(
            session, source_agent_id, str(draft_id), authorize_creator=authorize
        )
        return record.public()

    return await _governed(request, run)


@router.post("/{source_agent_id}/creation-drafts/{draft_id}/fork")
async def fork_creation_draft(
    request: Request,
    source_agent_id: str,
    draft_id: UUID,
    payload: AgentDraftForkRequest,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> Any:
    """A new editing draft copied from one of the requester's own revisions."""
    try:
        key = _IDEMPOTENCY_KEY.validate_python(idempotency_key)
    except ValidationError as exc:
        raise HTTPException(422, "Invalid Idempotency-Key") from exc

    async def run(session: Any, authorize: Any) -> Any:
        record, created = await _drafts(request).fork(
            session, source_agent_id, str(draft_id), payload, key, authorize_creator=authorize
        )
        return JSONResponse(record.public(), status_code=201 if created else 200)

    return await _governed(request, run)


@router.post("/{source_agent_id}/creation-drafts/{draft_id}/revisions/{revision}/review")
async def review_creation_draft_revision(
    request: Request, source_agent_id: str, draft_id: UUID, revision: int = Path(ge=1)
) -> Any:
    """G3.3 review of one exact committed revision, read by the server."""

    async def run(session: Any, authorize: Any) -> Any:
        return await _draft_lifecycle(request).review(
            session, source_agent_id, str(draft_id), revision, authorize_creator=authorize
        )

    return await _governed(request, run)


@router.post("/{source_agent_id}/creation-drafts/{draft_id}/approval")
async def approve_creation_draft_revision(
    request: Request, source_agent_id: str, draft_id: UUID, payload: DraftApprovalRequest
) -> Any:
    """Human approval of the exact reviewed revision; freezes the draft. Never creates."""

    async def run(session: Any, authorize: Any) -> Any:
        approval, record = await _draft_lifecycle(request).approve(
            session, source_agent_id, str(draft_id), payload, authorize_creator=authorize
        )
        return {"approval": approval.public(_approvals(request).now()), "draft": record.public()}

    return await _governed(request, run)


@router.post("/{source_agent_id}/creation-drafts/{draft_id}/reopen")
async def reopen_creation_draft(
    request: Request, source_agent_id: str, draft_id: UUID, payload: DraftReopenRequest
) -> Any:
    """Return an approved draft to editing when no creation is using its approval."""

    async def run(session: Any, authorize: Any) -> Any:
        record = await _draft_lifecycle(request).reopen(
            session, source_agent_id, str(draft_id), payload, authorize_creator=authorize
        )
        return record.public()

    return await _governed(request, run)


@router.get("/{source_agent_id}/creation-drafts/{draft_id}/proposals")
async def list_creation_draft_proposals(
    request: Request,
    source_agent_id: str,
    draft_id: UUID,
    state: str = "open",
    limit: int = 20,
    cursor: str | None = None,
) -> Any:
    """The owner's proposals for one of their drafts, newest first."""

    async def run(session: Any, authorize: Any) -> Any:
        proposals = _proposals(request)
        items, draft, following = await proposals.list_proposals(
            session,
            source_agent_id,
            str(draft_id),
            state=state,
            limit=limit,
            cursor=cursor,
            authorize_creator=authorize,
        )
        now = proposals.now()
        return {
            "contract": "caos.agent_draft_proposal_page",
            "schema_version": 1,
            "current_revision": draft.revision,
            "items": [item.public(now, draft) for item in items],
            "next_cursor": following,
        }

    return await _governed(request, run)


@router.get("/{source_agent_id}/creation-drafts/{draft_id}/revisions/{revision}")
async def get_creation_draft_revision(
    request: Request, source_agent_id: str, draft_id: UUID, revision: int = Path(ge=1)
) -> Any:
    """One immutable committed revision of the requester's own draft."""
    try:
        token = _token(request)
        async with authenticated_genesis(request) as session:
            record = await _drafts(request).revision(
                session,
                source_agent_id,
                str(draft_id),
                revision,
                authorize_creator=_creator_policy(request, token),
            )
            return record.public()
    except EventPermissionError as exc:
        raise HTTPException(403, "Agent draft denied") from exc
    except DraftError as exc:
        raise _draft_error(exc) from exc


@router.patch("/{source_agent_id}/creation-drafts/{draft_id}")
async def update_creation_draft(
    request: Request, source_agent_id: str, draft_id: UUID, payload: AgentDraftUpdateRequest
) -> Any:
    """Compare-and-set update; a stale ``expected_revision`` is a 409, never an overwrite."""
    try:
        token = _token(request)
        async with authenticated_genesis(request) as session:
            record, _ = await _drafts(request).update(
                session,
                source_agent_id,
                str(draft_id),
                payload,
                authorize_creator=_creator_policy(request, token),
            )
            return record.public()
    except EventPermissionError as exc:
        raise HTTPException(403, "Agent draft denied") from exc
    except DraftError as exc:
        raise _draft_error(exc) from exc


def _proposals(request: Request) -> Any:
    service = getattr(request.app.state, "agent_draft_proposals", None)
    if service is None:
        raise EventPermissionError("Draft proposals are not configured")
    return service


@router.get("/{source_agent_id}/creation-drafts/{draft_id}/proposals/{proposal_id}")
async def get_creation_draft_proposal(
    request: Request, source_agent_id: str, draft_id: UUID, proposal_id: UUID
) -> Any:
    """The owner's view of one proposal, with the fields that still apply."""
    try:
        token = _token(request)
        async with authenticated_genesis(request) as session:
            proposals = _proposals(request)
            proposal, draft = await proposals.get(
                session,
                source_agent_id,
                str(draft_id),
                str(proposal_id),
                authorize_creator=_creator_policy(request, token),
            )
            return proposal.public(proposals.now(), draft)
    except EventPermissionError as exc:
        raise HTTPException(403, "Draft proposal denied") from exc
    except DraftError as exc:
        raise _draft_error(exc) from exc


@router.post("/{source_agent_id}/creation-drafts/{draft_id}/proposals/{proposal_id}/decision")
async def decide_creation_draft_proposal(
    request: Request,
    source_agent_id: str,
    draft_id: UUID,
    proposal_id: UUID,
    payload: ProposalDecisionRequest,
) -> Any:
    """Explicit human acceptance (of selected fields) or rejection; never an approval."""
    try:
        token = _token(request)
        async with authenticated_genesis(request) as session:
            proposals = _proposals(request)
            proposal, draft = await proposals.decide(
                session,
                source_agent_id,
                str(draft_id),
                str(proposal_id),
                payload,
                authorize_creator=_creator_policy(request, token),
            )
            return {"proposal": proposal.public(proposals.now(), draft), "draft": draft.public()}
    except EventPermissionError as exc:
        raise HTTPException(403, "Draft proposal denied") from exc
    except DraftError as exc:
        raise _draft_error(exc) from exc


async def _lifecycle(request: Request, agent_id: str, operation: str) -> dict[str, Any]:
    try:
        async with authenticated_genesis(request) as session:
            key = AgentInstanceKey(session.tenant_id, session.user_id, agent_id, session.session_id)
            av = request.app.container.agentverse
            if operation == "awaken":
                result = await av.khepri().awaken(key)
            elif operation == "sleep":
                result = await av.anubis().sleep(key)
            else:
                result = await av.khepri().status(key)
            return {
                "agent_id": agent_id,
                "session_id": session.session_id,
                "state": result.state.value,
            }
    except EventPermissionError as exc:
        raise HTTPException(403, "Genesis operation denied") from exc


@router.post("/{agent_id}/{operation}")
async def lifecycle(
    request: Request, agent_id: str, operation: Literal["awaken", "sleep"]
) -> dict[str, Any]:
    return await _lifecycle(request, agent_id, operation)


@router.get("/{agent_id}")
async def status(request: Request, agent_id: str) -> dict[str, Any]:
    return await _lifecycle(request, agent_id, "status")


@router.post("/{agent_id}/signals/{signal}")
async def signal(request: Request, agent_id: str, signal: str, payload: dict[str, Any]) -> Any:
    try:
        async with authenticated_genesis(request) as session:
            key = AgentInstanceKey(session.tenant_id, session.user_id, agent_id, session.session_id)
            av = request.app.container.agentverse
            await av.utu_authority().authorize_lifecycle(key, operation="activate")
            runtime = av.instance_registry_v2().get(key)
            if runtime is None or not runtime.is_active:
                raise HTTPException(409, "Awaken this session's agent first")
            return await runtime.ncsc.request(signal, payload)
    except ValidationError as exc:
        raise HTTPException(422, "Invalid signal payload") from exc
    except EventPermissionError as exc:
        raise HTTPException(403, "Genesis signal denied") from exc
