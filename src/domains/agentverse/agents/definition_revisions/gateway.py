"""Human-to-validation adapter for Agent revisions (REV-2, ADR-0005).

The G3.3 authority chain, unchanged in kind: a live authenticated human session, a
reviewed ``revision_gateways`` binding ``(tenant, subject) → executor``, the executor's
session-origin UTU principal **attenuated** to ``agent.definition_revision.validate``
(whose only binding is the existing ``data.validation@1.0.0``), and a recheck of permit,
binding and authority around every dispatch. No grant, trusted line or signal is added:
attenuation only narrows the executor's existing ``data.validation`` root grant. The
adapter holds no write handle and never applies anything.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

from src.base.entities.user import User
from src.base.events.v2.contracts import EventPermissionError
from src.base.operations.v2.admission import AuthorizationProvenance, sha256_hex
from src.domains.agentverse.agents.composition import AgentComposer
from src.domains.agentverse.agents.composition.models import (
    CURRENT_V2_POLICY,
    CompositionContext,
    CompositionResult,
)
from src.domains.agentverse.agents.lifecycle.agent_principal_resolver import VerifiedAgentSession
from src.domains.agentverse.ncsc.cpbx import SynapticExchange
from src.domains.agentverse.operations.admission import permit_provenance
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey
from src.domains.agentverse.security.capability_scope import CapabilityScope
from src.domains.agentverse.security.revocation import ExecutionPermit
from src.domains.agentverse.security.utu import AuthorizedAgentPrincipal, UTUAuthority
from src.domains.agentverse.security.validation_gateway import VALIDATION_BINDING
from src.domains.agentverse.tools.validation_tool.models import ValidationResult
from src.domains.agentverse.tools.validation_tool.spec import (
    DATA_VALIDATION_PROVIDER,
    DATA_VALIDATION_SIGNAL,
    DATA_VALIDATION_VERSION,
)

REVISION_VALIDATION_OPERATION: Final = "agent.definition_revision.validate"
REVISION_VALIDATION_SCOPE: Final = CapabilityScope(
    REVISION_VALIDATION_OPERATION, VALIDATION_BINDING, frozenset({VALIDATION_BINDING})
)
REVISION_CAPABILITY: Final = {
    "signal": DATA_VALIDATION_SIGNAL,
    "provider": DATA_VALIDATION_PROVIDER,
    "version": DATA_VALIDATION_VERSION,
    "operation": REVISION_VALIDATION_OPERATION,
}


class RevisionGatewayBinding(BaseModel):
    """Reviewed deployment binding (policy file ``revision_gateways``). It installs no
    grant and no session membership; it only names which executor may validate the
    revisions of which human."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    tenant_id: str = Field(min_length=1, pattern=r"^\S+$")
    subject_id: str = Field(min_length=1, pattern=r"^\S+$")
    executor_id: str = Field(min_length=1, pattern=r"^\S+$")

    def fingerprint(self) -> str:
        """Binds reviews and approvals to this exact binding. Gateways are not part of the
        UTU policy revision, so a changed binding must be detected explicitly."""
        return sha256_hex({"contract": "caos.revision_gateway", "binding": self.model_dump()})


@dataclass(frozen=True)
class RevisionAuthority:
    """A verified human reviser. ``principal`` is the attenuated executor (never stored
    or returned to a client); ``authorization`` is one-way evidence."""

    requester: User
    # The verified session identity, equal to the requester's (checked at authorization).
    tenant_id: str
    subject_id: str
    binding: RevisionGatewayBinding
    principal: AuthorizedAgentPrincipal
    permit: ExecutionPermit
    authorization: AuthorizationProvenance


class AgentRevisionGateway:
    def __init__(
        self,
        *,
        bindings: tuple[RevisionGatewayBinding, ...],
        authority: UTUAuthority,
        exchange: SynapticExchange,
        composer: AgentComposer,
    ) -> None:
        entries = {(b.tenant_id, b.subject_id): b for b in bindings}
        if len(entries) != len(bindings):
            raise ValueError("Duplicate revision gateway binding")
        self._bindings: Mapping[tuple[str, str], RevisionGatewayBinding] = MappingProxyType(entries)
        self._authority = authority
        self._exchange = exchange
        self._composer = composer

    @property
    def authority(self) -> UTUAuthority:
        return self._authority

    async def authorize(self, session: VerifiedAgentSession) -> RevisionAuthority:
        if session.origin != "session" or not session.active:
            raise EventPermissionError("A live authenticated human session is required")
        binding = self._bindings.get((session.tenant_id, session.user_id))
        if binding is None:
            raise EventPermissionError("Revision gateway is not approved")
        principal = await self._authority.resolve_agent_principal(
            AgentInstanceKey(
                session.tenant_id, session.user_id, binding.executor_id, session.session_id
            )
        )
        if principal.permit.origin != "session":
            raise EventPermissionError("Bootstrap revision gateway denied")
        principal = await self._authority.attenuate(principal, REVISION_VALIDATION_SCOPE)
        permit = principal.permit
        requester = await self._authority.resource_requester(permit)
        if (requester.tenant_id, requester.id) != (session.tenant_id, session.user_id):
            raise EventPermissionError("Revision requester identity mismatch")
        return RevisionAuthority(
            requester=requester,
            tenant_id=session.tenant_id,
            subject_id=session.user_id,
            binding=binding,
            principal=principal,
            permit=permit,
            authorization=permit_provenance(permit, DATA_VALIDATION_SIGNAL),
        )

    async def recheck(self, auth: RevisionAuthority) -> User:
        """Permit, binding and current UTU authority are unchanged; returns the requester."""
        await self._authority.check_permit(auth.permit)
        if self._bindings.get((auth.binding.tenant_id, auth.binding.subject_id)) != auth.binding:
            raise EventPermissionError("Revision gateway binding changed")
        current = await self._authority.resolve_agent_principal(auth.permit.key)
        if current.permit.versions != auth.permit.versions:
            raise EventPermissionError("Revision authority changed")
        return await self._authority.resource_requester(auth.permit)

    async def validate(
        self,
        auth: RevisionAuthority,
        creation: dict[str, Any],
        check: Callable[[], Awaitable[None]],
    ) -> tuple[ValidationResult, CompositionResult]:
        """``data.validation`` over the complete candidate, then composition, under the
        attenuated executor; ``check`` re-verifies the target around the dispatch."""
        await self.recheck(auth)
        await check()
        client = self._exchange.bind(auth.principal)  # NEVER bind the unattenuated executor.
        try:
            result = await client.request(
                DATA_VALIDATION_SIGNAL,
                {"resource_type": "agent", "payload": creation},
                target=DATA_VALIDATION_PROVIDER,
                version=DATA_VALIDATION_VERSION,
            )
            validation = ValidationResult.model_validate(result["result"])
            await self.recheck(auth)
            composition = self._composer.compose(
                validation,
                context=CompositionContext.from_permit(auth.permit),
                policy=CURRENT_V2_POLICY,
            )
            await self.recheck(auth)
            await check()
        finally:
            client.revoke()
        return validation, composition
