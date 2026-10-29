"""Startup creation of the policy-pinned Enki identity, through the governed path only.

Not a provisioner and not an operator: this opens a trusted, policy-scoped bootstrap
lease (``RedisAgentSessionAuthority.bootstrap_lease``), lets UTU resolve JOSHUA under
it, binds JOSHUA's CPBX client exactly as Khepri's NCSC connector does (no cognitive
activation) and invokes the existing ``genesis.enki.request`` capability. Genesis then
runs validation, composition and the provisioned Store under its own UTU permit, and
Seshat attributes every record with ``authorization.origin == "bootstrap"``.

Reliability: an existing Enki is verified read-only and never rewritten, and nothing
is audited for unchanged state. A database error is not "missing". Concurrent starts
converge on one OPS V2 operation and one Store scope. Retryable outcomes (in progress,
audit unavailable, transient failures, interrupted operations awaiting takeover) are
retried until a deadline that exceeds the Genesis attempt lease; after it, or on a
definitive outcome, startup fails closed. Enki is never activated here.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Annotated, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.agents.genesis.initiation import (
    INITIATION_EVENT,
    SIGNAL,
    GenesisHandoff,
)
from src.domains.agentverse.audit.service import AuditSink
from src.domains.agentverse.ncsc.cpbx import SynapticExchange
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey
from src.domains.agentverse.security.sessions import RedisAgentSessionAuthority
from src.domains.agentverse.security.utu import UTUAuthority
from src.domains.agentverse.tools.store_tool import provisioned_key
from src.domains.agentverse.tools.store_tool.models import ProvisionedIdentity
from src.domains.agentverse.tools.store_tool.store.agent_store import scope_digest

logger = logging.getLogger(__name__)

PolicyId = Annotated[str, Field(min_length=1, max_length=256, pattern=r"^\S+$")]
_EXECUTION_EVENTS = (
    "genesis.validation.completed",
    "genesis.composition.completed",
    "genesis.store.completed",
)


class GenesisBootstrap(BaseModel):
    """Reviewed policy entry: who may be leased, for whom, and the pinned creation input."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tenant_id: PolicyId
    slot: Literal["enki"]
    subject_id: PolicyId
    agents: tuple[PolicyId, ...]
    creation: dict[str, Any]

    def check(self, handoff: GenesisHandoff, provision: ProvisionedIdentity) -> None:
        """The entry may only lease the handoff's two citizens for its pinned identity."""
        if (self.tenant_id, self.subject_id, self.slot) != (
            handoff.tenant_id,
            handoff.subject_id,
            handoff.slot,
        ):
            raise ValueError("Genesis bootstrap differs from its handoff")
        if sorted(self.agents) != sorted((handoff.initiator_id, handoff.executor_id)):
            raise ValueError("Genesis bootstrap may lease only its initiator and executor")
        if (self.creation.get("name"), self.creation.get("type")) != (
            provision.name,
            provision.type,
        ):
            raise ValueError("Genesis bootstrap creation differs from the pinned identity")
        profile = (self.creation.get("role_name"), self.creation.get("personality_key"))
        if provision.type == "form" and profile != ("enki", "empty_eva"):
            raise ValueError("Genesis bootstrap differs from the reviewed Enki composition")


class AgentDefinitions(Protocol):
    async def find_by_id(self, agent_id: str) -> Mapping[str, Any] | None: ...


class GenesisBootstrapError(RuntimeError):
    """Mandatory provisioning could not complete; startup must fail."""


class GenesisBootstrapConflict(GenesisBootstrapError):
    """An existing definition holds the pinned identity but is not compatible."""


class EnkiBootstrap:
    def __init__(
        self,
        *,
        entry: GenesisBootstrap,
        handoff: GenesisHandoff,
        provision: ProvisionedIdentity,
        definitions: AgentDefinitions,
        sessions: RedisAgentSessionAuthority,
        authority: UTUAuthority,
        exchange: SynapticExchange,
        log: AuditSink,
        deadline: float = 180.0,
        lease_seconds: float = 150.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        entry.check(handoff, provision)
        self._entry = entry
        self._handoff = handoff
        self._provision = provision
        self._definitions = definitions
        self._sessions = sessions
        self._authority = authority
        self._exchange = exchange
        self._log = log
        self._deadline = deadline
        self._lease_seconds = lease_seconds
        self._sleep = sleep
        self._clock = clock

    async def ensure(self) -> Literal["present", "created"]:
        # A read failure propagates: an unavailable database is not a missing Enki.
        if await self._present():
            return "present"
        deadline = self._clock() + self._deadline
        delay = 1.0
        while True:
            try:
                result = await self._initiate()
            except EventPermissionError as exc:
                # Redis/session unavailability surfaces as a denial; bounded retry.
                result = {"status": "denied", "retryable": True, "reason": str(exc)}
            status = result.get("status")
            if status == "provisioned":
                await self._confirm(result)
                logger.info("Genesis bootstrap created Enki (operation %s)", result["operation_id"])
                return "created"
            retryable = status in ("in_progress", "audit_unavailable") or (
                result.get("retryable") is True
            )
            detail = {k: result.get(k) for k in ("status", "stage", "issue_codes", "reason")}
            if not retryable:
                raise GenesisBootstrapError(f"Enki Genesis bootstrap ended definitively: {detail}")
            if self._clock() + delay > deadline:
                raise GenesisBootstrapError(f"Enki Genesis bootstrap missed its deadline: {detail}")
            logger.warning("Genesis bootstrap retrying in %.0fs: %s", delay, detail)
            await self._sleep(delay)
            delay = min(delay * 2, 15.0)
            # A concurrent start may have completed the same operation meanwhile.
            if await self._present():
                return "present"

    async def _present(self) -> bool:
        existing = await self._definitions.find_by_id(self._handoff.agent_id)
        if existing is None:
            return False
        self._verify(existing)
        return True

    async def _initiate(self) -> dict[str, Any]:
        entry, handoff = self._entry, self._handoff
        async with self._sessions.bootstrap_lease(
            tenant_id=entry.tenant_id,
            user_id=entry.subject_id,
            allowed_agent_ids=frozenset(entry.agents),
            seconds=self._lease_seconds,
        ) as session:
            key = AgentInstanceKey(
                entry.tenant_id, entry.subject_id, handoff.initiator_id, session.session_id
            )
            # Same resolution and binding as Khepri's NCSC connector; no runtime.
            principal = await self._authority.resolve_agent_principal(key)
            client = self._exchange.bind(principal)
            try:
                result = await client.request(SIGNAL, {"creation": dict(entry.creation)})
            finally:
                client.revoke()
                await client.drain()
        if not isinstance(result, dict):
            raise GenesisBootstrapError("Genesis initiation returned no result")
        return result

    def _verify(self, existing: Mapping[str, Any]) -> None:
        """Read-only compatibility; a conflicting identity is never overwritten."""
        entry, handoff, pinned = self._entry, self._handoff, self._provision
        expected = {
            "id": handoff.agent_id,
            "name": pinned.name,
            "type": pinned.type,
            "runtime_version": pinned.runtime_version,
            "role_name": entry.creation.get("role_name"),
            "personality_key": entry.creation.get("personality_key"),
            "tenant_id": entry.tenant_id,
            "creator": entry.subject_id,
            "ownership": "tenant",
        }
        mismatched = {k for k, v in expected.items() if existing.get(k) != v}
        store = existing.get("store")
        store = store if isinstance(store, Mapping) else {}
        authorization = store.get("authorization")
        authorization = authorization if isinstance(authorization, Mapping) else {}
        scope = scope_digest(
            tenant_id=entry.tenant_id,
            subject_id=entry.subject_id,
            principal_id=handoff.executor_id,
            key=provisioned_key(entry.slot),
        )
        if store.get("idempotency_scope") != scope or (
            authorization.get("tenant_id"),
            authorization.get("principal_id"),
            authorization.get("subject_id"),
        ) != (entry.tenant_id, handoff.executor_id, entry.subject_id):
            mismatched.add("store")
        if mismatched:
            raise GenesisBootstrapConflict(
                f"Existing Enki definition is incompatible: {sorted(mismatched)}"
            )

    async def _confirm(self, result: dict[str, Any]) -> None:
        """Startup completes only with the definition and its correlated evidence."""
        handoff = self._handoff
        if result.get("agent_id") != handoff.agent_id or not await self._present():
            raise GenesisBootstrapError("Genesis reported Enki without its definition")
        history = await self._log.history(self._entry.tenant_id, result["correlation_id"])
        initiation = next(
            (r for r in history if r.event_id == result.get("initiation_event_id")), None
        )
        if (
            initiation is None
            or initiation.event_type != INITIATION_EVENT
            or initiation.actor.principal_id != handoff.initiator_id
            or initiation.authorization.origin != "bootstrap"
        ):
            raise GenesisBootstrapError("Seshat lacks the bootstrap initiation evidence")
        for event_type in _EXECUTION_EVENTS:
            record = next(
                (
                    r
                    for r in history
                    if r.event_type == event_type
                    and r.outcome == "succeeded"
                    and r.actor.principal_id == handoff.executor_id
                ),
                None,
            )
            if record is None:
                raise GenesisBootstrapError(f"Seshat lacks {event_type} evidence")
            if event_type == "genesis.store.completed" and (
                record.subject.resource_id != handoff.agent_id
            ):
                raise GenesisBootstrapError("Seshat store evidence names another resource")
