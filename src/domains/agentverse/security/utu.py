"""UTU owns policy decisions and durable revocation; CPBX enforces them."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal
from uuid import uuid4

from src.base.events.v2.contracts import EventPermissionError
from src.base.events.v2.policy import EventPrincipal
from src.domains.agentverse.agents.lifecycle.agent_principal_resolver import AgentPrincipalResolver
from src.domains.agentverse.ncsc.authorization import SynapticAuthorization
from src.domains.agentverse.ncsc.models import SynapticExecutionContext, SynapticGrants
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey
from src.domains.agentverse.security.revocation import (
    ExecutionPermit,
    RevocationStore,
    policy_version,
)

LifecycleOperation = Literal["activate", "status", "deactivate"]


@dataclass(frozen=True)
class AuthorizedAgentPrincipal(EventPrincipal):
    permit: ExecutionPermit


class UTUAuthority:
    def __init__(
        self,
        *,
        resolver: AgentPrincipalResolver,
        authorization: SynapticAuthorization,
        revocations: RevocationStore | None = None,
        poll_interval: float = 0.5,
    ) -> None:
        if poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        self._resolver = resolver
        self._authorization = authorization
        self._revocations = revocations
        self._version = policy_version(authorization)
        self.poll_interval = poll_interval
        binder = getattr(resolver._sessions, "bind_revocation_handler", None)
        self._sessions_bound = callable(binder)
        if callable(binder):
            binder(self.revoke)
        self._listeners: list[Callable[[str, str | None, str | None], None]] = []
        self._blocked: set[tuple[str, str | None, str | None]] = set()
        self._work: dict[asyncio.Task[Any], tuple[ExecutionPermit, int]] = {}

    def subscribe_revocation(self, listener: Callable[[str, str | None, str | None], None]) -> None:
        """Trusted local supervisor callback; closes gates before its first await."""
        self._listeners.append(listener)

    def _store(self) -> RevocationStore:
        if self._revocations is None:
            raise EventPermissionError("UTU revocation authority is not bound")
        return self._revocations

    def reload_policy(self, authorization: SynapticAuthorization) -> None:
        """Load a deployed snapshot into this singleton; Redis versions still decide.

        This does not grant permission or mutate authoritative state. Workers can
        call this after another worker commits install_policy's CAS transition.
        """
        self._authorization = authorization
        self._resolver.replace_policy(authorization)
        self._version = policy_version(authorization)

    async def install_policy(
        self, tenant_id: str, *, expected: str | None, authorization: SynapticAuthorization
    ) -> str:
        """Trusted administrative CAS. Stale workers deny until explicitly reloaded."""
        version = uuid4().hex + ":" + policy_version(authorization)
        await self._store().install_policy(tenant_id, expected=expected, version=version)
        self.reload_policy(authorization)
        # Old issued permits cannot survive any policy version transition.
        for task, (permit, _) in tuple(self._work.items()):
            if permit.key.tenant_id == tenant_id and permit.versions[0] != version:
                task.cancel()
        for listener in self._listeners:
            listener(tenant_id, None, None)
        return version

    @staticmethod
    def matches(key: AgentInstanceKey, tenant: str, agent: str | None, session: str | None) -> bool:
        return (
            key.tenant_id == tenant
            and (agent is None or key.agent_id == agent)
            and (session is None or key.session_id == session)
        )

    async def revoke(
        self, tenant_id: str, *, agent_id: str | None = None, session_id: str | None = None
    ) -> bool:
        """Trusted control-plane method, never exposed as a caller capability."""
        if agent_id is not None and session_id is not None:
            raise ValueError("Revoke exactly one scope")
        self._blocked.add((tenant_id, agent_id, session_id))
        for listener in self._listeners:
            listener(tenant_id, agent_id, session_id)
        for task, (permit, _) in tuple(self._work.items()):
            if self.matches(permit.key, tenant_id, agent_id, session_id):
                task.cancel()
        return await self._store().revoke(tenant_id, agent_id=agent_id, session_id=session_id)

    async def check_permit(self, permit: ExecutionPermit) -> None:
        if not isinstance(permit, ExecutionPermit):
            raise EventPermissionError("UTU execution permit required")
        if any(self.matches(permit.key, *scope) for scope in self._blocked):
            raise EventPermissionError("UTU scope revoked locally")
        sessions = self._resolver._sessions
        validate_session = getattr(type(sessions), "check_execution_session", None)
        if callable(validate_session):
            await validate_session(sessions, permit.key)
        versions = await self._store().read(permit.key)
        if (
            any(self.matches(permit.key, *scope) for scope in self._blocked)
            or versions != permit.versions
            or versions[0].split(":")[-1] != self._version
        ):
            raise EventPermissionError("UTU execution permit superseded")

    async def resolve_agent_principal(self, key: AgentInstanceKey) -> AuthorizedAgentPrincipal:
        if not self._sessions_bound:
            raise EventPermissionError("UTU session invalidation adapter is not bound")
        # Capture before external verification; a revoke/reload during it cannot issue authority.
        versions = await self._store().read(key)
        principal = await self._resolver(key)
        permit = ExecutionPermit(key, versions)
        await self.check_permit(permit)
        return AuthorizedAgentPrincipal(principal.agent_id, principal.tenant_id, permit)

    async def authorize_lifecycle(
        self, key: AgentInstanceKey, *, operation: LifecycleOperation
    ) -> None:
        if operation == "activate":
            await self.resolve_agent_principal(key)
        elif operation in ("status", "deactivate"):
            await self._resolver.verify_lifecycle_caller(key)
        else:
            raise EventPermissionError("Unknown lifecycle operation")

    def grants_for(self, principal: EventPrincipal) -> SynapticGrants:
        return self._authorization.grants_for(principal)

    def require_invoke(self, *, context: SynapticExecutionContext, signal: str) -> None:
        self._authorization.require_invoke(context=context, signal=signal)

    @asynccontextmanager
    async def execution(
        self, *, context: SynapticExecutionContext, signal: str
    ) -> AsyncIterator[None]:
        permit = context.permit
        if not isinstance(permit, ExecutionPermit) or (
            permit.key.tenant_id,
            permit.key.agent_id,
        ) != (context.tenant_id, context.principal_id):
            raise EventPermissionError("UTU verified session permit required")
        await self.check_permit(permit)
        self.require_invoke(context=context, signal=signal)
        task = asyncio.current_task()
        assert task is not None
        previous = self._work.get(task)
        self._work[task] = (permit, previous[1] + 1 if previous else 1)

        async def watch() -> None:
            while True:
                await asyncio.sleep(self.poll_interval)
                try:
                    await self.check_permit(permit)
                except EventPermissionError:
                    task.cancel()
                    for listener in self._listeners:
                        listener(permit.key.tenant_id, permit.key.agent_id, permit.key.session_id)
                    return

        watcher = asyncio.create_task(watch())
        try:
            yield
            # Cancellation suppression by a handler cannot produce an authorized result.
            await self.check_permit(permit)
        finally:
            watcher.cancel()
            count = self._work[task][1]
            if count == 1:
                del self._work[task]
            else:
                self._work[task] = (permit, count - 1)
            await asyncio.gather(watcher, return_exceptions=True)
