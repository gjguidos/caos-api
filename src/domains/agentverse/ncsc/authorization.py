from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from itertools import pairwise
from typing import Protocol

from src.base.events.v2.contracts import EventPermissionError
from src.base.events.v2.policy import EventPrincipal
from src.domains.agentverse.ncsc.models import (
    SynapticExecutionContext,
    SynapticGrants,
    TrustedLine,
)


class SynapticPolicy(Protocol):
    """Decision interface enforced by CPBX, including UTU's policy facade."""

    def require_invoke(self, *, context: SynapticExecutionContext, signal: str) -> None: ...

    def execution(
        self, *, context: SynapticExecutionContext, signal: str
    ) -> AbstractAsyncContextManager[None]: ...


class SynapticAuthorization:
    """Default-deny local CPBX policy, independent of broker event permissions.

    A root grant delegates only through explicitly configured trusted lines.
    Recheck the root and every edge for the original tenant/principal on every
    invocation. Contexts and configuration must come from trusted runtime code;
    Python objects are not an isolation boundary for hostile in-process code.
    """

    def __init__(
        self,
        *,
        grants: Mapping[tuple[str, str], SynapticGrants] | None = None,
        trusted_lines: Iterable[TrustedLine] = (),
    ) -> None:
        self._grants = dict(grants or {})
        if any(not isinstance(value, SynapticGrants) for value in self._grants.values()):
            raise TypeError("CPBX requires SynapticGrants, not broker event grants")
        self._trusted_lines = frozenset(trusted_lines)

    @asynccontextmanager
    async def execution(
        self, *, context: SynapticExecutionContext, signal: str
    ) -> AsyncIterator[None]:
        self.require_invoke(context=context, signal=signal)
        yield

    def grants_for(self, principal: EventPrincipal) -> SynapticGrants:
        """Read an immutable tenant/principal grant from this policy snapshot."""
        return self._grants.get((principal.tenant_id, principal.agent_id), SynapticGrants())

    def require_invoke(self, *, context: SynapticExecutionContext, signal: str) -> None:
        def deny() -> None:
            raise EventPermissionError(
                f"Invoke denied: {context.tenant_id}/{context.principal_id} -> {signal}"
            )

        if not context.tenant_id.strip() or not context.principal_id.strip():
            deny()
        chain = context.invocation_chain
        # active_signal alone is not proof of an authorized parent invocation.
        if (not chain and context.active_signal is not None) or (
            chain and context.active_signal != chain[-1]
        ):
            deny()
        path = (*chain, signal)
        # Bound delegation depth and reject recursive capability loops.
        if len(path) > 64 or len(set(path)) != len(path):
            deny()
        grants = self._grants.get((context.tenant_id, context.principal_id))
        if grants is None or path[0] not in grants.invoke:
            deny()
        for source, target in pairwise(path):
            if not any(
                line.source == source
                and line.target == target
                and line.tenant_id in (None, context.tenant_id)
                and line.principal_id in (None, context.principal_id)
                for line in self._trusted_lines
            ):
                deny()
