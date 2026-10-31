"""Governed Activity Context Service (AGENT-CTX-0.1, ADR-0004).

Trusted, in-process shared infrastructure: not a signal, Tool or route, and it grants
nothing. It knows nothing about what an activity means: registered contracts declare
typed slots, and registered resolvers (owned by each resource's domain) re-resolve every
reference read-only for the verified actor.

Concurrency:
- The per-scope head (``_id`` = scope) is the only authority for the active generation.
  Activation is one head compare-and-set that also records the *activation intent*
  (the full successor and the predecessor to close), so at most one generation is ever
  active and an interrupted start/switch is completed exactly as decided.
- Context transitions are single-document compare-and-set with operation-ID replay.
- Writes require the owner's live attempt (a conversation turn lease) that is not older
  than the last writer. These are checks, not Redis/Mongo atomicity.

Evidence: every consequential transition commits its Seshat obligation in the same
document write (outbox). Delivery is idempotent by deterministic event ID and is retried
by any later authorized operation or ``reconcile`` on the scope.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, ConfigDict

from src.base.entities.user import User
from src.base.events.v2.contracts import EventPermissionError
from src.base.operations.v2.admission import AuthorizationProvenance, sha256_hex
from src.domains.agentverse.activities.models import (
    MAX_APPLIED,
    MAX_GENERATIONS,
    ActivationIntent,
    ActivityActiveExists,
    ActivityClosed,
    ActivityContext,
    ActivityContractUnavailable,
    ActivityInvalidReferences,
    ActivityLimit,
    ActivityNotFound,
    ActivityOperationConflict,
    ActivityRef,
    ActivityReferenceUnavailable,
    ActivityRevisionConflict,
    ActivityScopeHead,
    ActivityStaleOwner,
    ActorScope,
    AppliedOperation,
    Causation,
    ClosedReason,
    EvidenceObligation,
    EvidenceType,
    OwnerRef,
    ResourceRef,
)
from src.domains.agentverse.activities.owners import OwnerResolver
from src.domains.agentverse.activities.registry import (
    ActivityContract,
    ActivityContractRegistry,
    ResolvedResource,
    ResourceResolverRegistry,
)
from src.domains.agentverse.activities.repository import MongoActivityContextRepository
from src.domains.agentverse.audit.models import (
    ActivityContextMetadata,
    ActivityEvidenceReference,
    LogCorrelation,
    LogEvent,
    LogSubject,
    LogTarget,
    stable_event_id,
)
from src.domains.agentverse.audit.repository import LogConflict, LogError
from src.domains.agentverse.operations.admission import permit_provenance
from src.domains.agentverse.security.revocation import ExecutionPermit
from src.domains.agentverse.security.utu import AuthorizedAgentPrincipal, UTUAuthority

_RETRIES = 5


@dataclass(frozen=True)
class ActivityActor:
    """An Agent acting for its human in a live session (verified permit only)."""

    tenant_id: str
    subject_id: str
    principal_id: str
    session_id: str
    requester: User
    grants: frozenset[str]
    # One-way evidence of the verified permit for a given owner signal. The owner kind,
    # not the actor, names the signal (CONVERSATION-0.1A): no universal turn capability.
    provenance: Callable[[str], AuthorizationProvenance] = field(repr=False, compare=False)

    @property
    def scope(self) -> ActorScope:
        return ActorScope(
            subject_id=self.subject_id, principal_id=self.principal_id, principal_kind="agent"
        )

    @classmethod
    async def from_principal(
        cls, authority: UTUAuthority, principal: AuthorizedAgentPrincipal
    ) -> ActivityActor:
        permit = principal.permit
        if not isinstance(permit, ExecutionPermit) or permit.origin != "session":
            raise EventPermissionError("Activity contexts require an Agent in a live human session")
        await authority.check_permit(permit)
        requester = await authority.resource_requester(permit)
        key = permit.key
        if requester.id != key.user_id:
            raise EventPermissionError("Activity requester identity mismatch")
        return cls(
            tenant_id=key.tenant_id,
            subject_id=key.user_id,
            principal_id=key.agent_id,
            session_id=key.session_id,
            requester=requester,
            grants=frozenset(authority.grants_for(principal).invoke),
            provenance=partial(permit_provenance, permit),
        )


class ActivityReferenceView(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: str
    id: str
    observed_revision: int


class ActivityContextView(BaseModel):
    """Requester projection: no actor internals, causation, fences, operations or
    pending evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    contract: str = "caos.activity_context_view"
    context_id: str
    activity: str
    owner_kind: str
    owner_id: str
    generation: int
    status: str
    closed_reason: str | None
    previous_context_id: str | None
    revision: int
    references: dict[str, ActivityReferenceView]


@dataclass(frozen=True)
class ActivityRefresh:
    """Read-only re-resolution of a context's references through their owners."""

    context: ActivityContext
    resolved: dict[str, ResolvedResource]
    unavailable: dict[str, str]


@dataclass(frozen=True)
class ActiveGeneration:
    """The scope's active generation and whether it is settled (CONVERSATION-0.1A).

    ``materialized``: the generation's own document is stored and active. A decided
    successor (head intent) whose document is missing is *not* materialized.
    ``reconciliation_pending``: an activation intent is still recorded on the head, or
    evidence obligations are undelivered; replay or ``reconcile`` settles both.
    """

    context: ActivityContext
    materialized: bool
    reconciliation_pending: bool


def view(context: ActivityContext) -> ActivityContextView:
    return ActivityContextView(
        context_id=context.context_id,
        activity=context.activity.key,
        owner_kind=context.owner.kind,
        owner_id=context.owner.id,
        generation=context.generation,
        status=context.status,
        closed_reason=context.closed_reason,
        previous_context_id=context.previous_context_id,
        revision=context.revision,
        references={
            slot: ActivityReferenceView(kind=r.kind, id=r.id, observed_revision=r.observed_revision)
            for slot, r in context.references.items()
        },
    )


class ActivityContextService:
    def __init__(
        self,
        *,
        repository: MongoActivityContextRepository,
        contracts: ActivityContractRegistry,
        resolvers: ResourceResolverRegistry,
        owners: dict[str, OwnerResolver],
        audit: Any,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not (contracts.frozen and resolvers.frozen):
            raise ValueError("Activity registries must be frozen")
        self._repository = repository
        self._contracts = contracts
        self._resolvers = resolvers
        self._owners = dict(owners)
        self._audit = audit
        self._clock = clock

    # --- Reads (side-effect free) -----------------------------------------------------------

    async def active(self, actor: ActivityActor, owner: OwnerRef) -> ActivityContext | None:
        await self._authorize(actor, owner)
        head = await self._repository.head(actor.tenant_id, self._scope_key(actor, owner))
        if head is None or head.active_context_id is None:
            return None
        if head.intent is not None:
            return head.intent.successor  # Decided; its document may still be pending.
        context = await self._repository.get(actor.tenant_id, head.active_context_id)
        return context if context is not None and context.status == "active" else None

    async def current(self, actor: ActivityActor, owner: OwnerRef) -> ActiveGeneration | None:
        """Like ``active``, distinguishing a materialized generation from a decided one
        still awaiting reconciliation. Read-only: it never completes an intent."""
        await self._authorize(actor, owner)
        head = await self._repository.head(actor.tenant_id, self._scope_key(actor, owner))
        if head is None or head.active_context_id is None:
            return None
        stored = await self._repository.get(actor.tenant_id, head.active_context_id)
        materialized = stored is not None and stored.status == "active"
        if head.intent is not None:
            return ActiveGeneration(
                context=stored if materialized and stored else head.intent.successor,
                materialized=materialized,
                reconciliation_pending=True,
            )
        if stored is None or not materialized:
            return None
        return ActiveGeneration(
            context=stored,
            materialized=True,
            reconciliation_pending=bool(stored.pending_evidence),
        )

    async def history(self, actor: ActivityActor, owner: OwnerRef) -> list[ActivityContext]:
        """Every generation of the actor's scope, oldest first (closed ones included)."""
        await self._authorize(actor, owner)
        scope = self._scope_key(actor, owner)
        head = await self._repository.head(actor.tenant_id, scope)
        if head is None or head.generation == 0:
            return []
        ids = [self._context_id(scope, n) for n in range(1, head.generation + 1)]
        found = {c.context_id: c for c in await self._repository.get_many(actor.tenant_id, ids)}
        if head.intent is not None:
            found.setdefault(head.intent.successor.context_id, head.intent.successor)
        return sorted(found.values(), key=lambda c: c.generation)

    async def get(self, actor: ActivityActor, context_id: str) -> ActivityContext:
        context = await self._repository.get(actor.tenant_id, context_id)
        if context is None or (context.tenant_id, context.actor) != (actor.tenant_id, actor.scope):
            raise ActivityNotFound("Activity context not found")
        await self._authorize(actor, context.owner)
        return context

    async def refresh(self, actor: ActivityActor, context_id: str) -> ActivityRefresh:
        context = await self.get(actor, context_id)
        resolved: dict[str, ResolvedResource] = {}
        unavailable: dict[str, str] = {}
        for slot, reference in context.references.items():
            try:
                resolved[slot] = await self._resolvers.get(reference.kind).resolve(
                    actor, reference.id
                )
            except ActivityReferenceUnavailable as exc:
                unavailable[slot] = exc.code
        return ActivityRefresh(context, resolved, unavailable)

    # --- Transitions ------------------------------------------------------------------------

    async def start(
        self,
        actor: ActivityActor,
        attempt: Any,
        *,
        owner: OwnerRef,
        activity: str,
        references: dict[str, str],
        operation_id: str,
    ) -> tuple[ActivityContext, bool]:
        """Activate the next generation of a registered activity; idempotent per operation."""
        owner_resolver = await self._authorize(actor, owner)
        contract = self._contract(activity, owner)
        digest = sha256_hex(
            {
                "op": "start",
                "owner": owner.model_dump(),
                "activity": contract.key(),
                "fingerprint": contract.fingerprint(),
                "references": dict(sorted(references.items())),
            }
        )
        head = await self._reconcile_scope(actor, owner)
        existing = await self._created_by(actor, owner, operation_id)
        if existing is not None:
            return self._same_start(existing, digest), False
        if head is not None and head.active_context_id is not None:
            raise ActivityActiveExists("An activity is already active for this owner")
        await self._declared(owner_resolver, actor, owner, contract.key())
        latest = await self._latest(actor, head)
        causation = await self._causation(
            owner_resolver, actor, owner, attempt, operation_id, latest
        )
        resolved = await self._resolve(actor, contract, references, start=True)
        successor = self._successor(
            actor, owner, contract, resolved, head, latest, causation, digest, "start"
        )
        await self._activate(actor, owner, head, successor, operation_id, digest, None)
        return await self._stored(actor, successor), True

    async def switch(
        self,
        actor: ActivityActor,
        attempt: Any,
        *,
        context_id: str,
        expected_revision: int,
        activity: str,
        references: dict[str, str],
        operation_id: str,
    ) -> ActivityContext:
        """Close the active generation (``switched``) and activate the next, decided by one
        head compare-and-set; an interruption is completed from the recorded intent."""
        current = await self.get(actor, context_id)
        owner = current.owner
        owner_resolver = self._owners[owner.kind]
        contract = self._contract(activity, owner)
        digest = sha256_hex(
            {
                "op": "switch",
                "context_id": context_id,
                "expected_revision": expected_revision,
                "activity": contract.key(),
                "fingerprint": contract.fingerprint(),
                "references": dict(sorted(references.items())),
            }
        )
        head = await self._reconcile_scope(actor, owner)
        existing = await self._created_by(actor, owner, operation_id)
        if existing is not None:
            return self._same_start(existing, digest)
        current = await self.get(actor, context_id)
        if (
            head is None
            or head.active_context_id != current.context_id
            or current.status != "active"
        ):
            raise ActivityClosed("The activity context is not active")
        if expected_revision != current.revision:
            raise ActivityRevisionConflict(current.revision)
        await self._declared(owner_resolver, actor, owner, contract.key())
        causation = await self._causation(
            owner_resolver, actor, owner, attempt, operation_id, current
        )
        resolved = await self._resolve(actor, contract, references, start=True)
        successor = self._successor(
            actor, owner, contract, resolved, head, current, causation, digest, "switch"
        )
        await self._activate(
            actor, owner, head, successor, operation_id, digest, current.context_id
        )
        return await self._stored(actor, successor)

    async def advance(
        self,
        actor: ActivityActor,
        attempt: Any,
        *,
        context_id: str,
        expected_revision: int,
        operation_id: str,
        references: dict[str, str] | None = None,
    ) -> ActivityContext:
        """Re-observe every reference's revision and apply slot updates allowed by the
        contract (``replace``/``successor``; ``immutable`` slots never change)."""
        changes = dict(references or {})
        current = await self.get(actor, context_id)
        digest = sha256_hex(
            {
                "op": "advance",
                "expected_revision": expected_revision,
                "references": dict(sorted(changes.items())),
            }
        )
        head = await self._reconcile_scope(actor, current.owner)
        current = await self.get(actor, context_id)
        if current.replay(operation_id, digest):
            return current
        self._writable(head, current, expected_revision)
        causation = await self._causation(
            self._owners[current.owner.kind], actor, current.owner, attempt, operation_id, current
        )
        contract = self._contracts.resolve(current.activity.key, current.activity.fingerprint)
        unknown = set(changes) - {slot.name for slot in contract.slots}
        if unknown:
            raise ActivityInvalidReferences("Unknown activity slot")
        wanted: dict[str, str] = {}
        for slot in contract.slots:
            previous = current.references.get(slot.name)
            target = changes.get(slot.name, previous.id if previous else None)
            if target is None:
                continue
            if previous is not None and target != previous.id and slot.update == "immutable":
                raise ActivityInvalidReferences(f"Slot {slot.name} is immutable")
            wanted[slot.name] = target
        resolved = await self._resolve(actor, contract, wanted, start=False)
        for slot in contract.slots:
            previous = current.references.get(slot.name)
            moved = resolved.get(slot.name)
            if previous is None or moved is None or moved.id == previous.id:
                continue
            if slot.update == "successor":
                before = await self._resolvers.get(slot.kind).resolve(actor, previous.id)
                if moved.predecessor_id != previous.id or (moved.version, moved.fingerprint) != (
                    before.version,
                    before.fingerprint,
                ):
                    raise ActivityReferenceUnavailable(slot.name, "not_successor")
        return await self._transition(
            actor,
            current,
            causation,
            operation_id,
            digest,
            references=self._references(resolved),
        )

    async def close(
        self,
        actor: ActivityActor,
        attempt: Any,
        *,
        context_id: str,
        expected_revision: int,
        operation_id: str,
        reason: ClosedReason,
    ) -> ActivityContext:
        if reason == "switched":
            raise ValueError("Use switch() to activate another generation")
        current = await self.get(actor, context_id)
        digest = sha256_hex(
            {"op": "close", "expected_revision": expected_revision, "reason": reason}
        )
        head = await self._reconcile_scope(actor, current.owner)
        current = await self.get(actor, context_id)
        if not current.replay(operation_id, digest):
            self._writable(head, current, expected_revision)
            causation = await self._causation(
                self._owners[current.owner.kind],
                actor,
                current.owner,
                attempt,
                operation_id,
                current,
            )
            current = await self._transition(
                actor, current, causation, operation_id, digest, close=reason
            )
        await self._release_head(actor, current)
        await self._deliver(actor, current)
        return await self.get(actor, context_id)

    async def reconcile(self, actor: ActivityActor, owner: OwnerRef) -> int:
        """Authorized, bounded recovery of one scope: complete a decided activation, clear
        a stale active pointer and deliver pending evidence. Returns obligations left."""
        await self._authorize(actor, owner)
        await self._reconcile_scope(actor, owner)
        return sum(len(c.pending_evidence) for c in await self.history(actor, owner))

    # --- Internals --------------------------------------------------------------------------

    async def _authorize(self, actor: ActivityActor, owner: OwnerRef) -> OwnerResolver:
        resolver = self._owners.get(owner.kind)
        if resolver is None:
            raise ActivityNotFound("Activity context not found")
        await resolver.authorize(actor, owner.id)
        return resolver

    @staticmethod
    async def _declared(
        owner_resolver: OwnerResolver, actor: ActivityActor, owner: OwnerRef, activity: str
    ) -> None:
        """A new generation (start or switch) needs the owner kind's explicit consent
        (REV-5: ``conversation.v2`` consults the Agent's current definition). MIG-FIX-0.1:
        an owner kind without a consent source never consents. Reads, advances, closes,
        replays and recovery of a decided intent are never blocked, and consent grants
        nothing: the owner's own authorization still applies."""
        permits = getattr(owner_resolver, "permits_activity", None)
        if permits is None or not await permits(actor, owner.id, activity):
            raise ActivityContractUnavailable(
                "The Agent's definition does not declare this activity"
            )

    def _contract(self, activity: str, owner: OwnerRef) -> ActivityContract:
        contract = self._contracts.resolve(activity)
        if owner.kind not in contract.owner_kinds:
            raise ActivityContractUnavailable("Activity contract does not apply to this owner")
        return contract

    @staticmethod
    def _scope_key(actor: ActivityActor, owner: OwnerRef) -> str:
        return sha256_hex(
            {
                "tenant_id": actor.tenant_id,
                "owner": owner.model_dump(),
                "subject_id": actor.subject_id,
                "principal_id": actor.principal_id,
            }
        )

    @staticmethod
    def _context_id(scope_key: str, generation: int) -> str:
        return str(uuid5(NAMESPACE_URL, f"caos:activity-context:{scope_key}:{generation}"))

    async def _stored(self, actor: ActivityActor, context: ActivityContext) -> ActivityContext:
        """The committed state (evidence possibly settled), as a replay would return it."""
        return await self._repository.get(actor.tenant_id, context.context_id) or context

    async def _latest(
        self, actor: ActivityActor, head: ActivityScopeHead | None
    ) -> ActivityContext | None:
        if head is None or head.generation == 0:
            return None
        return await self._repository.get(
            actor.tenant_id, self._context_id(head.scope_key, head.generation)
        )

    async def _created_by(
        self, actor: ActivityActor, owner: OwnerRef, operation_id: str
    ) -> ActivityContext | None:
        for context in await self.history(actor, owner):
            if context.created_by.operation_id == operation_id:
                return context
        return None

    @staticmethod
    def _same_start(context: ActivityContext, digest: str) -> ActivityContext:
        if context.start_digest != digest:
            raise ActivityOperationConflict("Operation ID reused for another activation")
        return context

    @staticmethod
    def _writable(
        head: ActivityScopeHead | None, context: ActivityContext, expected_revision: int
    ) -> None:
        if (
            head is None
            or head.active_context_id != context.context_id
            or context.status != "active"
        ):
            raise ActivityClosed("The activity context is not active")
        if expected_revision != context.revision:
            raise ActivityRevisionConflict(context.revision)

    async def _causation(
        self,
        owner_resolver: OwnerResolver,
        actor: ActivityActor,
        owner: OwnerRef,
        attempt: Any,
        operation_id: str,
        newest: ActivityContext | None,
    ) -> Causation:
        causation = await owner_resolver.causation(actor, owner.id, attempt, operation_id)
        if newest is not None and causation.fence < newest.last_owner.fence:
            raise ActivityStaleOwner("A newer owner attempt already wrote this scope")
        return causation

    async def _resolve(
        self,
        actor: ActivityActor,
        contract: ActivityContract,
        references: dict[str, str],
        *,
        start: bool,
    ) -> dict[str, ResolvedResource]:
        names = {slot.name for slot in contract.slots}
        if not set(references) <= names:
            raise ActivityInvalidReferences("Unknown activity slot")
        resolved: dict[str, ResolvedResource] = {}
        for slot in contract.slots:
            resource_id = references.get(slot.name)
            if resource_id is None:
                if slot.required:
                    raise ActivityInvalidReferences(f"Slot {slot.name} is required")
                continue
            if not isinstance(resource_id, str):
                raise ActivityInvalidReferences(f"Slot {slot.name} needs a resource ID")
            try:
                found = await self._resolvers.get(slot.kind).resolve(actor, resource_id)
            except ActivityReferenceUnavailable as exc:
                raise ActivityReferenceUnavailable(slot.name) from exc
            if found.kind != slot.kind or found.id != resource_id:
                raise ActivityReferenceUnavailable(slot.name)
            allowed = slot.start_statuses if start else slot.update_statuses
            if found.status not in allowed:
                raise ActivityReferenceUnavailable(slot.name, "state")
            resolved[slot.name] = found
        for link in contract.links:
            source, target = resolved.get(link.source), resolved.get(link.target)
            if source is None or target is None:
                continue
            related = source.link(link.name)
            if related is None or (related.kind, related.id) != (target.kind, target.id):
                raise ActivityReferenceUnavailable(link.source, "inconsistent")
        return resolved

    @staticmethod
    def _references(resolved: dict[str, ResolvedResource]) -> dict[str, ResourceRef]:
        return {
            slot: ResourceRef(kind=r.kind, id=r.id, observed_revision=r.revision)
            for slot, r in resolved.items()
        }

    def _successor(
        self,
        actor: ActivityActor,
        owner: OwnerRef,
        contract: ActivityContract,
        resolved: dict[str, ResolvedResource],
        head: ActivityScopeHead | None,
        previous: ActivityContext | None,
        causation: Causation,
        digest: str,
        origin: str,
    ) -> ActivityContext:
        scope = self._scope_key(actor, owner)
        generation = (head.generation if head is not None else 0) + 1
        if generation > MAX_GENERATIONS:
            raise ActivityLimit("Too many activity generations for this owner")
        references = self._references(resolved)
        now = self._clock()
        fields: dict[str, Any] = {
            "context_id": self._context_id(scope, generation),
            "tenant_id": actor.tenant_id,
            "scope_key": scope,
            "actor": actor.scope,
            "owner": owner,
            "activity": ActivityRef(key=contract.key(), fingerprint=contract.fingerprint()),
            "generation": generation,
            "status": "active",
            "previous_context_id": None if previous is None else previous.context_id,
            "references": references,
            "revision": 1,
            "origin": origin,
            "start_digest": digest,
            "started_references": references,
            "created_by": causation,
            "last_owner": causation,
            "applied": [
                AppliedOperation(operation_id=causation.operation_id, digest=digest, revision=1)
            ],
            "created_at": now,
            "updated_at": now,
        }
        event: EvidenceType = (
            "activity.context.switched" if origin == "switch" else "activity.context.started"
        )
        draft = ActivityContext.model_construct(**fields)
        fields["pending_evidence"] = [self._obligation(draft, event)]
        return ActivityContext.build(**fields)

    @staticmethod
    def _obligation(context: ActivityContext, event_type: EvidenceType) -> EvidenceObligation:
        closed = event_type == "activity.context.closed"
        references = context.references if closed else context.started_references
        return EvidenceObligation(
            event_id=stable_event_id(
                correlation_id=context.context_id, event_type=event_type, step="transition"
            ),
            event_type=event_type,
            causation_id=(context.last_owner if closed else context.created_by).event_id,
            metadata=ActivityContextMetadata(
                event_version=1,
                context_id=context.context_id,
                activity=context.activity.key,
                activity_fingerprint=context.activity.fingerprint,
                owner_kind=context.owner.kind,
                owner_id=context.owner.id,
                principal_id=context.actor.principal_id,
                generation=context.generation,
                revision=context.revision if closed else 1,
                references=tuple(
                    ActivityEvidenceReference(
                        slot=slot, kind=r.kind, resource_id=r.id, revision=r.observed_revision
                    )
                    for slot, r in sorted(references.items())
                ),
                previous_context_id=context.previous_context_id,
                closed_reason=context.closed_reason if closed else None,
            ),
        )

    async def _activate(
        self,
        actor: ActivityActor,
        owner: OwnerRef,
        head: ActivityScopeHead | None,
        successor: ActivityContext,
        operation_id: str,
        digest: str,
        close_previous: str | None,
    ) -> None:
        """Decide the activation with one head compare-and-set, then complete it."""
        if head is None:
            head = await self._repository.create_head(
                ActivityScopeHead.build(
                    scope_key=successor.scope_key,
                    tenant_id=actor.tenant_id,
                    actor=actor.scope,
                    owner=owner,
                    generation=0,
                    revision=0,
                    updated_at=self._clock(),
                )
            )
        if (
            head.intent is not None
            or head.active_context_id != close_previous
            or (head.generation + 1 != successor.generation)
        ):
            raise ActivityActiveExists("Another activation was decided first")
        following = ActivityScopeHead.build(
            **{
                **head.model_dump(exclude={"state_digest"}),
                "generation": successor.generation,
                "active_context_id": successor.context_id,
                "intent": ActivationIntent(
                    operation_id=operation_id,
                    digest=digest,
                    successor=successor,
                    close_previous=close_previous,
                ),
                "revision": head.revision + 1,
                "updated_at": max(self._clock(), head.updated_at),
            }
        )
        if not await self._repository.replace_head(head, following):
            current = await self._repository.head(actor.tenant_id, head.scope_key)
            if (
                current is None
                or current.intent is None
                or (current.intent.operation_id != operation_id)
            ):
                raise ActivityActiveExists("Another activation was decided first")
            following = current  # An identical concurrent request decided it.
        await self._complete(actor, following)

    async def _complete(self, actor: ActivityActor, head: ActivityScopeHead) -> ActivityScopeHead:
        """Apply a decided activation: close the predecessor, insert the successor, clear
        the intent. Every step is idempotent; repeating after a crash converges."""
        intent = head.intent
        assert intent is not None
        if intent.close_previous is not None:
            await self._close_previous(actor, intent)
        await self._repository.insert(intent.successor)
        for _ in range(_RETRIES):
            cleared = ActivityScopeHead.build(
                **{
                    **head.model_dump(exclude={"state_digest", "intent"}),
                    "intent": None,
                    "revision": head.revision + 1,
                    "updated_at": max(self._clock(), head.updated_at),
                }
            )
            if await self._repository.replace_head(head, cleared):
                head = cleared
                break
            current = await self._repository.head(actor.tenant_id, head.scope_key)
            if (
                current is None
                or current.intent is None
                or (current.intent.operation_id != intent.operation_id)
            ):
                head = current or head
                break  # Another worker completed it.
            head = current
        stored = await self._repository.get(actor.tenant_id, intent.successor.context_id)
        if stored is not None:
            await self._deliver(actor, stored)
        return head

    async def _close_previous(self, actor: ActivityActor, intent: ActivationIntent) -> None:
        for _ in range(_RETRIES):
            previous = await self._repository.get(actor.tenant_id, intent.close_previous or "")
            if previous is None or previous.status == "closed":
                return
            following = ActivityContext.build(
                **{
                    **previous.model_dump(exclude={"state_digest"}),
                    "status": "closed",
                    "closed_reason": "switched",
                    "revision": previous.revision + 1,
                    "last_owner": intent.successor.created_by
                    if intent.successor.created_by.fence >= previous.last_owner.fence
                    else previous.last_owner,
                    "applied": [
                        *previous.applied,
                        AppliedOperation(
                            operation_id=intent.operation_id,
                            digest=intent.digest,
                            revision=previous.revision + 1,
                        ),
                    ][-MAX_APPLIED:],
                    "updated_at": max(self._clock(), previous.updated_at),
                }
            )
            if await self._repository.replace(previous, following):
                return
        raise ActivityRevisionConflict(0)

    async def _transition(
        self,
        actor: ActivityActor,
        current: ActivityContext,
        causation: Causation,
        operation_id: str,
        digest: str,
        *,
        references: dict[str, ResourceRef] | None = None,
        close: ClosedReason | None = None,
    ) -> ActivityContext:
        revision = current.revision + 1
        fields = {
            **current.model_dump(exclude={"state_digest"}),
            "revision": revision,
            "last_owner": causation,
            "applied": [
                *current.applied,
                AppliedOperation(operation_id=operation_id, digest=digest, revision=revision),
            ][-MAX_APPLIED:],
            "updated_at": max(self._clock(), current.updated_at),
        }
        if references is not None:
            fields["references"] = references
        if close is not None:
            fields.update(status="closed", closed_reason=close)
            draft = ActivityContext.model_construct(
                **{
                    **fields,
                    "references": current.references,
                    "actor": current.actor,
                    "owner": current.owner,
                    "activity": current.activity,
                    "created_by": current.created_by,
                    "last_owner": causation,
                }
            )
            fields["pending_evidence"] = [
                *current.pending_evidence,
                self._obligation(draft, "activity.context.closed"),
            ]
        following = ActivityContext.build(**fields)
        if await self._repository.replace(current, following):
            return following
        stored = await self._repository.get(actor.tenant_id, current.context_id)
        if stored is not None and stored.replay(operation_id, digest):
            return stored  # An identical concurrent operation applied it.
        if stored is not None and stored.last_owner.fence > causation.fence:
            raise ActivityStaleOwner("A newer owner attempt already changed the context")
        raise ActivityRevisionConflict(stored.revision if stored else current.revision)

    async def _release_head(self, actor: ActivityActor, context: ActivityContext) -> None:
        """Clear the active pointer of a closed context (idempotent; repairable later)."""
        for _ in range(_RETRIES):
            head = await self._repository.head(actor.tenant_id, context.scope_key)
            if head is None or head.active_context_id != context.context_id or head.intent:
                return
            released = ActivityScopeHead.build(
                **{
                    **head.model_dump(exclude={"state_digest"}),
                    "active_context_id": None,
                    "revision": head.revision + 1,
                    "updated_at": max(self._clock(), head.updated_at),
                }
            )
            if await self._repository.replace_head(head, released):
                return

    async def _reconcile_scope(
        self, actor: ActivityActor, owner: OwnerRef
    ) -> ActivityScopeHead | None:
        scope = self._scope_key(actor, owner)
        head = await self._repository.head(actor.tenant_id, scope)
        if head is not None and head.intent is not None:
            head = await self._complete(actor, head)
        if head is not None and head.active_context_id is not None and head.intent is None:
            active = await self._repository.get(actor.tenant_id, head.active_context_id)
            if active is not None and active.status == "closed":
                await self._release_head(actor, active)
                head = await self._repository.head(actor.tenant_id, scope)
        if head is not None and head.generation:
            ids = [self._context_id(scope, n) for n in range(1, head.generation + 1)]
            for context in await self._repository.get_many(actor.tenant_id, ids):
                if context.pending_evidence:
                    await self._deliver(actor, context)
        return head

    async def _deliver(self, actor: ActivityActor, context: ActivityContext) -> None:
        """Record pending obligations in Seshat (look up first; a conflicting concurrent
        write counts as recorded), then settle them. Failures leave them pending."""
        if not context.pending_evidence:
            return
        target = self._owners[context.owner.kind]
        delivered: set[str] = set()
        for obligation in context.pending_evidence:
            try:
                if await self._audit.get(context.tenant_id, obligation.event_id) is None:
                    await self._audit.append(
                        LogEvent(
                            event_id=obligation.event_id,
                            event_type=obligation.event_type,
                            outcome="succeeded",
                            subject=LogSubject(
                                resource_type="activity_context", resource_id=context.context_id
                            ),
                            target=LogTarget(
                                signal=target.audit_signal, version=target.audit_version
                            ),
                            correlation=LogCorrelation(
                                correlation_id=context.owner.id,
                                causation_id=obligation.causation_id,
                            ),
                            metadata=obligation.metadata.model_dump(mode="json"),
                        ),
                        authorization=actor.provenance(target.audit_signal),
                    )
            except LogConflict:
                pass  # Recorded concurrently.
            except LogError:
                break  # Stays pending and discoverable; a later reconciliation retries.
            delivered.add(obligation.event_id)
        if not delivered:
            return
        for _ in range(_RETRIES):
            remaining = [e for e in context.pending_evidence if e.event_id not in delivered]
            settled = ActivityContext.build(
                **{
                    **context.model_dump(exclude={"state_digest", "pending_evidence"}),
                    "pending_evidence": remaining,
                }
            )
            if await self._repository.replace(context, settled):
                return
            current = await self._repository.get(context.tenant_id, context.context_id)
            if current is None or not {e.event_id for e in current.pending_evidence} & delivered:
                return
            context = current
