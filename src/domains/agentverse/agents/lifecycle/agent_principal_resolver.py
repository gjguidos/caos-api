"""Trusted Genesis activation boundary; no request body is identity evidence."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import NoReturn, Protocol

from src.base.events.v2.contracts import EventPermissionError
from src.base.events.v2.policy import EventPrincipal
from src.domains.agentverse.ncsc.authorization import SynapticAuthorization
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey


@dataclass(frozen=True, slots=True)
class AgentActivationApproval:
    """Server-provisioned ownership/enablement; never read from client tool config.

    Legacy definitions lack tenant and enablement fields. An explicit approval
    attests those facts until a trusted persisted authorization store exists.
    """

    tenant_id: str
    agent_id: str
    owner_user_id: str
    enabled: bool = False


@dataclass(frozen=True, slots=True)
class VerifiedAgentSession:
    """An authority's current, authenticated session record, not a caller claim.

    The authority must check expiry/revocation and tenant membership on lookup.
    It must bind the session to the current authenticated execution context
    (or a server-approved background lease). A lookup by UUID alone is unsafe.
    allowed_agent_ids authorizes activation, independently of CPBX root grants.
    """

    tenant_id: str
    user_id: str
    session_id: str
    allowed_agent_ids: frozenset[str] = frozenset()
    active: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowed_agent_ids", frozenset(self.allowed_agent_ids))


class AgentSessionAuthority(Protocol):
    """Return a record only for the authenticated caller/lease authorized now.

    Reject unknown, expired, revoked, or caller-mismatched sessions. Do not
    manufacture a record from AgentInstanceKey, payload, or unverified headers.
    """

    async def get_session(self, session_id: str) -> VerifiedAgentSession | None: ...


class RevocableAgentSessionAuthority(AgentSessionAuthority, Protocol):
    """Trusted logout/expiry/membership adapter for long-lived execution.

    Persist invalidation through the supplied UTU handler before acknowledging
    logout/expiry/removal. Schedule expiry durably; do not rely on a local timer.
    All session writers must use this path. Without this adapter UTU denies starts.
    """

    def bind_revocation_handler(self, handler: Callable[..., Awaitable[bool]]) -> None: ...


class UnboundAgentSessionAuthority:
    async def get_session(self, session_id: str) -> VerifiedAgentSession | None:
        raise EventPermissionError("Genesis session authority is not bound")


class PersistedAgentDefinitions(Protocol):
    """The existing DBService.find_by_id contract; no runtime construction."""

    async def find_by_id(self, agent_id: str) -> Mapping[str, object] | None: ...


class AgentPrincipalResolver:
    """Resolve an agent identity only after session, ownership and policy checks.

    Approvals are a trusted configuration snapshot. Public/group/admin read
    access does not grant activation. Khepri's caller identity is never used as
    the agent principal. Missing adapters/configuration deny by default.
    """

    def __init__(
        self,
        *,
        definitions: PersistedAgentDefinitions,
        sessions: AgentSessionAuthority,
        authorization: SynapticAuthorization,
        approvals: Mapping[tuple[str, str], AgentActivationApproval] | None = None,
    ) -> None:
        self._definitions = definitions
        self._sessions = sessions
        self._authorization = authorization
        self._approvals = dict(approvals or {})
        for scope, approval in self._approvals.items():
            if not isinstance(approval, AgentActivationApproval):
                raise TypeError("Genesis requires trusted AgentActivationApproval records")
            if scope != (approval.tenant_id, approval.agent_id):
                raise ValueError("Genesis approval scope mismatch")
            if any(
                not isinstance(v, str) or not v.strip()
                for v in (approval.tenant_id, approval.agent_id, approval.owner_user_id)
            ):
                raise ValueError("Genesis approval identity must be non-empty")

    def replace_policy(self, authorization: SynapticAuthorization) -> None:
        self._authorization = authorization

    async def __call__(self, key: AgentInstanceKey) -> EventPrincipal:
        principal = await self.verify_lifecycle_caller(key, require_enabled=True)
        if not self._authorization.grants_for(principal).invoke:
            raise EventPermissionError("Agent activation denied")
        return principal

    async def verify_lifecycle_caller(
        self, key: AgentInstanceKey, *, require_enabled: bool = False
    ) -> EventPrincipal:
        """Verify owner authority independently of the agent's CPBX grants.

        Status and stopping remain possible for an approved owner when an agent
        is disabled or loses capability grants. Session binding, allowed agent,
        approval ownership and persisted ownership must still match.
        """

        def deny() -> NoReturn:
            raise EventPermissionError("Agent activation denied")

        # A session UUID or payload user_id alone proves nothing. Re-read the
        # authoritative record for every resolution, including reconnects.
        session = await self._sessions.get_session(key.session_id)
        if not isinstance(session, VerifiedAgentSession):
            deny()
        if (
            session.active is not True
            or (session.tenant_id, session.user_id, session.session_id)
            != (key.tenant_id, key.user_id, key.session_id)
            or key.agent_id not in session.allowed_agent_ids
        ):
            deny()

        approval = self._approvals.get((key.tenant_id, key.agent_id))
        if (
            approval is None
            or (require_enabled and approval.enabled is not True)
            or approval.owner_user_id != key.user_id
        ):
            deny()

        definition = await self._definitions.find_by_id(key.agent_id)
        if not isinstance(definition, Mapping):
            deny()
        if definition.get("id") != key.agent_id or definition.get("creator") != key.user_id:
            deny()
        # Respect explicit persisted restrictions if supplied by a future store.
        # They may narrow the server approval but can never broaden it.
        if "tenant_id" in definition and definition["tenant_id"] != key.tenant_id:
            deny()
        if require_enabled and "enabled" in definition and definition["enabled"] is not True:
            deny()
        if require_enabled and definition.get("disabled", False) is not False:
            deny()

        return EventPrincipal(agent_id=key.agent_id, tenant_id=key.tenant_id)
