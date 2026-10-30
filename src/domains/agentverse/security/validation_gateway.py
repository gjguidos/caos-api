"""Explicit, read-only human-to-validation adapter. No provisioning or activation handle."""

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from src.base.entities.user import User
from src.base.events.v2.contracts import EventPermissionError
from src.base.operations.v2.admission import AuthorizationProvenance, sha256_hex
from src.domains.agentverse.agents.composition import AgentComposer
from src.domains.agentverse.agents.composition.models import (
    CURRENT_V2_POLICY,
    CompositionContext,
    V2CompositionPolicy,
)
from src.domains.agentverse.agents.lifecycle.agent_principal_resolver import VerifiedAgentSession
from src.domains.agentverse.ncsc.cpbx import SynapticExchange
from src.domains.agentverse.operations.admission import permit_provenance
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey
from src.domains.agentverse.security import agent_access
from src.domains.agentverse.security.capability_scope import CapabilityBinding, CapabilityScope
from src.domains.agentverse.security.revocation import ExecutionPermit
from src.domains.agentverse.security.utu import UTUAuthority
from src.domains.agentverse.tools.validation_tool.models import ValidationResult
from src.domains.agentverse.tools.validation_tool.spec import (
    DATA_VALIDATION_PROVIDER,
    DATA_VALIDATION_SIGNAL,
    DATA_VALIDATION_VERSION,
)

VALIDATION_BINDING = CapabilityBinding(
    DATA_VALIDATION_SIGNAL, DATA_VALIDATION_PROVIDER, DATA_VALIDATION_VERSION
)
VALIDATION_SCOPE = CapabilityScope(
    "agent.creation_draft.validate", VALIDATION_BINDING, frozenset({VALIDATION_BINDING})
)
REVIEW_CONTRACT = "caos.agent_creation_review"
REVIEW_TTL = timedelta(minutes=10)
CAPABILITY = {
    "signal": DATA_VALIDATION_SIGNAL,
    "provider": DATA_VALIDATION_PROVIDER,
    "version": DATA_VALIDATION_VERSION,
    "operation": VALIDATION_SCOPE.operation,
}
# Reviewed candidate fields a requester may see. Backend selectors, the DNA rule and
# V1 routes stay server-side; the full candidate is bound by the review digest.
PREVIEW_FIELDS = (
    "runtime_version",
    "name",
    "system_name",
    "type",
    "role_name",
    "personality_key",
    "prompt",
    "tools",
    "workflows",
    "rule_pack_ids",
    "visibility",
    "whitelisted_groups",
    "gui_components",
)


class ValidationGatewayBinding(BaseModel):
    """Reviewed deployment binding. This never installs grants or session membership."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    tenant_id: str = Field(min_length=1, pattern=r"^\S+$")
    subject_id: str = Field(min_length=1, pattern=r"^\S+$")
    source_agent_id: str = Field(min_length=1, pattern=r"^\S+$")
    executor_id: str = Field(min_length=1, pattern=r"^\S+$")


class AgentDraftValidationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    contract: Literal["caos.agent_creation_draft"]
    schema_version: StrictInt = Field(ge=1, le=1)
    resource_type: Literal["agent"]
    creation: dict[str, Any]


@dataclass(frozen=True)
class DraftReview:
    """One authoritative review. ``snapshot`` is internal: it is bound by ``digest`` and
    persisted only by trusted approval code; ``envelope`` is the public projection."""

    envelope: dict[str, Any]
    snapshot: dict[str, Any] | None
    digest: str | None
    eligible: bool
    requester: User
    permit: ExecutionPermit
    validated_at: datetime
    # Re-verifies permit, gateway binding, source readability and current authority.
    recheck: Callable[[], Awaitable[User]]


@dataclass(frozen=True)
class DraftAuthority:
    """A verified human draft author: the trusted requester and one-way UTU evidence
    of the authority it acted under. Never a permit."""

    requester: User
    authorization: AuthorizationProvenance


def _policy(policy: V2CompositionPolicy) -> dict[str, Any]:
    return {
        key: sorted(value) if isinstance(value, frozenset) else value
        for key, value in policy.model_dump().items()
    }


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


class AgentDraftValidationGateway:
    def __init__(
        self,
        *,
        bindings: tuple[ValidationGatewayBinding, ...],
        authority: UTUAuthority,
        exchange: SynapticExchange,
        read_definition: Callable[[str], Awaitable[dict[str, Any] | None]],
        composer: AgentComposer,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        entries = {(b.tenant_id, b.subject_id, b.source_agent_id): b for b in bindings}
        if len(entries) != len(bindings):
            raise ValueError("Duplicate validation gateway binding")
        self._bindings: Mapping[tuple[str, str, str], ValidationGatewayBinding] = MappingProxyType(
            entries
        )
        self._authority = authority
        self._exchange = exchange
        # A single read, never the definition service: the gateway holds no write handle.
        self._read_definition = read_definition
        self._composer = composer
        self._clock = clock

    async def attribute(
        self, session: VerifiedAgentSession, executor_id: str
    ) -> AuthorizationProvenance:
        """Current UTU provenance of a live human session through the gateway executor.

        Attribution evidence only (e.g. for a revocation): it executes nothing and is
        never a permit. Bootstrap or missing authority fails closed.
        """
        if session.origin != "session" or not session.active:
            raise EventPermissionError("A live authenticated human session is required")
        principal = await self._authority.resolve_agent_principal(
            AgentInstanceKey(session.tenant_id, session.user_id, executor_id, session.session_id)
        )
        if principal.permit.origin != "session":
            raise EventPermissionError("Bootstrap attribution denied")
        principal = await self._authority.attenuate(principal, VALIDATION_SCOPE)
        return permit_provenance(principal.permit, DATA_VALIDATION_SIGNAL)

    async def requester(
        self, session: VerifiedAgentSession, source_agent_id: str
    ) -> DraftAuthority:
        """The trusted requester who may author creation drafts for this source.

        The same human-to-source authority as :meth:`review` (live session, approved
        gateway binding, session-origin UTU authority, readable source) without any
        dispatch: it executes nothing, and the permit is neither returned nor kept.
        """
        if session.origin != "session" or not session.active:
            raise EventPermissionError("A live authenticated human session is required")
        binding = self._bindings.get((session.tenant_id, session.user_id, source_agent_id))
        if binding is None:
            raise EventPermissionError("Validation gateway is not approved")
        principal = await self._authority.resolve_agent_principal(
            AgentInstanceKey(
                session.tenant_id, session.user_id, binding.executor_id, session.session_id
            )
        )
        if principal.permit.origin != "session":
            raise EventPermissionError("Bootstrap validation gateway denied")
        principal = await self._authority.attenuate(principal, VALIDATION_SCOPE)
        requester = await self._authority.resource_requester(principal.permit)
        source = await self._read_definition(source_agent_id)
        if (
            not source
            or source.get("id") != source_agent_id
            or not self._authority.can_access_resource(requester, source, operation="READ")
        ):
            raise EventPermissionError("Validation source unavailable")
        return DraftAuthority(
            requester, permit_provenance(principal.permit, DATA_VALIDATION_SIGNAL)
        )

    async def validate(
        self,
        session: VerifiedAgentSession,
        source_agent_id: str,
        request: AgentDraftValidationRequest,
    ) -> dict[str, Any]:
        return (await self.review(session, source_agent_id, request)).envelope

    async def review(
        self,
        session: VerifiedAgentSession,
        source_agent_id: str,
        request: AgentDraftValidationRequest,
    ) -> DraftReview:
        if session.origin != "session" or not session.active:
            raise EventPermissionError("A live authenticated human session is required")
        binding = self._bindings.get((session.tenant_id, session.user_id, source_agent_id))
        if binding is None:
            raise EventPermissionError("Validation gateway is not approved")
        principal = await self._authority.resolve_agent_principal(
            AgentInstanceKey(
                session.tenant_id, session.user_id, binding.executor_id, session.session_id
            )
        )
        if principal.permit.origin != "session":
            raise EventPermissionError("Bootstrap validation gateway denied")
        principal = await self._authority.attenuate(principal, VALIDATION_SCOPE)
        permit = principal.permit

        async def check() -> User:
            await self._authority.check_permit(permit)
            current = self._bindings.get((session.tenant_id, session.user_id, source_agent_id))
            if current != binding:
                raise EventPermissionError("Validation gateway binding changed")
            requester = await self._authority.resource_requester(permit)
            source = await self._read_definition(source_agent_id)
            if (
                not source
                or source.get("id") != source_agent_id
                or not self._authority.can_access_resource(requester, source, operation="READ")
            ):
                raise EventPermissionError("Validation source unavailable")
            # Approval, membership and grants must still be current, not just the
            # once-issued permit. Resolution has no lifecycle activation effects.
            current_principal = await self._authority.resolve_agent_principal(permit.key)
            if current_principal.permit.versions != permit.versions:
                raise EventPermissionError("Validation authority changed")
            return requester

        await check()
        validated_at = self._clock()
        client = self._exchange.bind(principal)  # NEVER bind the unattenuated executor.
        try:
            result = await client.request(
                DATA_VALIDATION_SIGNAL,
                {
                    "resource_type": request.resource_type,
                    "payload": request.creation,
                },
                target=DATA_VALIDATION_PROVIDER,
                version=DATA_VALIDATION_VERSION,
            )
            validation = ValidationResult.model_validate(result["result"])
            await check()
            composition = self._composer.compose(
                validation,
                context=CompositionContext.from_permit(permit),
                policy=CURRENT_V2_POLICY,
            )
            requester = await check()
        finally:
            client.revoke()

        reasons = sorted(
            {issue.code for issue in validation.issues}
            | {issue.code for issue in composition.issues}
            | (set() if agent_access.can_create(requester) else {"requester.cannot_create"})
        )
        eligible = validation.valid and composition.composed and not reasons
        snapshot = None
        digest = None
        if eligible:
            # Everything the decision depends on; no time, so an unchanged draft under
            # unchanged authority reproduces the digest at approval.
            snapshot = {
                "contract": REVIEW_CONTRACT,
                "schema_version": 1,
                "tenant_id": permit.key.tenant_id,
                "requester": {
                    "subject_id": requester.id,
                    "roles": sorted(set(requester.roles)),
                    "scopes": sorted(set(requester.scopes)),
                },
                "source_agent_id": source_agent_id,
                "executor_id": binding.executor_id,
                "capability": CAPABILITY,
                "draft": {
                    "contract": request.contract,
                    "schema_version": request.schema_version,
                    "resource_type": request.resource_type,
                },
                "normalized": validation.normalized,
                "normalized_digest": validation.normalized_digest,
                "candidate": composition.candidate,
                "resolved": validation.resolved,
                "policy": {
                    "utu_policy_revision": permit.versions[0],
                    "composition": _policy(CURRENT_V2_POLICY),
                },
            }
            digest = sha256_hex(snapshot)

        # Resolved internal dependencies and backend selectors are not a public
        # manifest. The human sees their normalized input and the reviewed projection.
        safe_validation = validation.model_dump(mode="json")
        safe_validation["resolved"] = None
        for issue in safe_validation["issues"]:
            issue["message"] = "Configuration is invalid or unavailable for this requester"
        preview = None
        if composition.composed and composition.candidate is not None:
            preview = {key: composition.candidate[key] for key in PREVIEW_FIELDS}
        envelope = {
            "contract": REVIEW_CONTRACT,
            "schema_version": 1,
            "source_agent_id": source_agent_id,
            "validation": safe_validation,
            "composition": {
                "compatible": composition.composed,
                "issues": [
                    {
                        "path": issue.path,
                        "code": issue.code,
                        "message": "V2 composition is unavailable",
                    }
                    for issue in composition.issues
                ],
                "preview": preview,
                "runtime_execution_authorized": False,
            },
            "eligibility": {"eligible": eligible, "reasons": reasons},
            "review_digest": digest,
            "validated_at": _timestamp(validated_at),
            "expires_at": _timestamp(validated_at + REVIEW_TTL),
            "capability": CAPABILITY,
            # A review, and an approval of it, never authorize these.
            "authorizes": {"creation": False, "activation": False},
        }
        return DraftReview(
            envelope=envelope,
            snapshot=snapshot,
            digest=digest,
            eligible=eligible,
            requester=requester,
            permit=permit,
            validated_at=validated_at,
            recheck=check,
        )
