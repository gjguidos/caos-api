"""Governed Agent revision authoring and review (REV-2, ADR-0005).

Agent-neutral. Every operation re-establishes the human reviser through the revision
gateway and the target through its owning record: a governed definition in the
requester's tenant whose ``creator`` is the requester and which the requester may read
and write. Missing, foreign and hidden targets are indistinguishable.

The predecessor is either the latest **stored** revision record (whose definition must
equal the current projection) or, before any record exists, a **verified** creation
baseline that is *not materialized*: its digests are those revision 1 would have, and no
read, edit or review creates it. Unverified, conflicted or unavailable baselines cannot
be revised (a governed adoption procedure is future work).

Nothing here approves (REV-3), applies or projects (REV-4) a revision, writes
``coll_agents``, attaches a Tool or grants a capability.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from src.base.entities.user import User
from src.base.events.v2.contracts import EventPermissionError
from src.base.operations.v2.admission import sha256_hex
from src.domains.agentverse.agents.composition.models import CURRENT_V2_POLICY
from src.domains.agentverse.agents.conversational_composition_v2 import (
    ConversationComposition,
    resolve_conversation,
)
from src.domains.agentverse.agents.definition_revisions.baseline import (
    baseline_revision,
    verify_baseline,
)
from src.domains.agentverse.agents.definition_revisions.canonical import (
    CanonicalizationError,
    canonical_definition,
    definition_digest,
)
from src.domains.agentverse.agents.definition_revisions.drafts import (
    MongoRevisionDraftRepository,
    PredecessorRef,
    RevisionDraft,
    RevisionDraftConflict,
    RevisionDraftCreateRequest,
    RevisionDraftIdempotencyConflict,
    RevisionDraftNotFound,
    RevisionDraftUnavailable,
    RevisionDraftUpdateRequest,
    revision_draft_id,
)
from src.domains.agentverse.agents.definition_revisions.gateway import (
    REVISION_CAPABILITY,
    AgentRevisionGateway,
    RevisionAuthority,
)
from src.domains.agentverse.agents.definition_revisions.repository import (
    MongoAgentDefinitionRevisionRepository,
)
from src.domains.agentverse.agents.definition_revisions.review import (
    MongoRevisionReviewRepository,
    ReviewState,
    RevisionReview,
    RevisionReviewNotCurrent,
    RevisionReviewNotFound,
    review_id,
)
from src.domains.agentverse.entities.agent import Agent
from src.domains.agentverse.runtime.compatibility import is_governed_definition
from src.domains.agentverse.security import agent_access
from src.domains.agentverse.security.validation_gateway import PREVIEW_FIELDS, _policy
from src.domains.agentverse.tools.store_tool.models import ProvisionedIdentity
from src.domains.agentverse.tools.validation_tool.models import AgentCreationInput

REVIEW_TTL: Final = timedelta(minutes=10)  # Same lifetime as a G3.3 creation review.
# Insertion time is excluded from revision digests; a fixed value makes the not-yet-
# materialized baseline's digests deterministic without storing anything.
_BASELINE_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
# Definition fields a reviewed candidate replaces (from the composed candidate);
# everything else is carried unchanged from the predecessor.
COMPOSED_FIELDS: Final = (
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


class RevisionTargetNotFound(EventPermissionError):
    """Missing, foreign, not governed or not the requester's: indistinguishable."""


class RevisionBaselineUnavailable(Exception):
    """The current definition cannot serve as a predecessor (not a ``verified`` baseline,
    or a projection that differs from its latest stored revision)."""

    def __init__(self, status: str, codes: tuple[str, ...] = ()) -> None:
        super().__init__("The current definition cannot be revised")
        self.code = f"revision.baseline_{status}"
        self.codes = codes


ConversationResolver = Callable[[Mapping[str, Any]], ConversationComposition]
Policy = Callable[[User], Awaitable[None]]
HolderReader = Callable[[str], Awaitable[list[dict[str, Any]]]]


class AgentDefinitionRevisionService:
    def __init__(
        self,
        *,
        gateway: AgentRevisionGateway,
        holders: HolderReader,
        revisions: MongoAgentDefinitionRevisionRepository,
        drafts: MongoRevisionDraftRepository,
        reviews: MongoRevisionReviewRepository,
        provisions: Mapping[str, ProvisionedIdentity] | None = None,
        conversation: ConversationResolver = resolve_conversation,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._gateway = gateway
        self._holders = holders
        self._revisions = revisions
        self._drafts = drafts
        self._reviews = reviews
        self._provisions = dict(provisions or {})
        self._conversation = conversation
        self._clock = clock

    # --- Authority and target -------------------------------------------------------------

    async def _authorize(self, session: Any, policy: Policy | None) -> RevisionAuthority:
        """The verified reviser, then the route's existing human policy (role and
        ``AGENT_WRITE`` permission from the same bearer); either denial is uniform."""
        auth = await self._gateway.authorize(session)
        if policy is not None:
            await policy(auth.requester)
        return auth

    async def _target(
        self, auth: RevisionAuthority, agent_id: str
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        holders = await self._holders(agent_id)
        tenant = auth.tenant_id
        owned = [d for d in holders if d.get("tenant_id") == tenant and d.get("id") == agent_id]
        if len(owned) != 1:
            raise RevisionTargetNotFound("Agent not found")
        (document,) = owned
        requester = auth.requester
        try:
            agent = Agent.model_validate(document)
        except ValueError:
            raise RevisionTargetNotFound("Agent not found") from None
        if (
            not is_governed_definition(document)
            or document.get("creator") != requester.id
            or not self._gateway.authority.can_access_resource(
                requester, document, operation="READ"
            )
            or not agent_access.can_write(requester, agent)
        ):
            raise RevisionTargetNotFound("Agent not found")
        return document, holders

    async def _predecessor(
        self, tenant_id: str, agent_id: str, document: dict[str, Any], holders: list[dict[str, Any]]
    ) -> PredecessorRef:
        chain = await self._revisions.chain(tenant_id, agent_id)
        if chain:
            latest = chain[-1]
            try:
                current = definition_digest(tenant_id, agent_id, canonical_definition(document))
            except CanonicalizationError:
                current = None
            if current != latest.definition_digest:
                raise RevisionBaselineUnavailable("conflicted", ("projection.mismatch",))
            return PredecessorRef(
                revision=latest.revision,
                revision_digest=latest.revision_digest,
                definition_digest=latest.definition_digest,
                materialized=True,
            )
        verification = verify_baseline(tenant_id, agent_id, holders)
        if verification.status != "verified":
            raise RevisionBaselineUnavailable(verification.status, verification.codes)
        baseline = baseline_revision(verification, document, recorded_at=_BASELINE_EPOCH)
        return PredecessorRef(
            revision=1,
            revision_digest=baseline.revision_digest,
            definition_digest=baseline.definition_digest,
            materialized=False,
        )

    async def baseline(
        self, session: Any, agent_id: str, *, policy: Policy | None = None
    ) -> dict[str, Any]:
        """Read-only: the requester's own Agent's baseline status and predecessor."""
        auth = await self._authorize(session, policy)
        document, holders = await self._target(auth, agent_id)
        verification = verify_baseline(auth.tenant_id, agent_id, holders)
        summary = {
            key: value
            for key, value in verification.summary().items()
            if key in ("agent_id", "status", "codes", "creation_evidence", "revision_ready")
        }
        try:
            predecessor = await self._predecessor(auth.tenant_id, agent_id, document, holders)
        except RevisionBaselineUnavailable:
            predecessor = None
        return {
            "contract": "caos.agent_definition_baseline",
            "schema_version": 1,
            **summary,
            "predecessor": None
            if predecessor is None
            else {
                "revision": predecessor.revision,
                "revision_digest": predecessor.revision_digest,
                "materialized": predecessor.materialized,
            },
        }

    # --- Drafts ---------------------------------------------------------------------------

    async def create_draft(
        self,
        session: Any,
        agent_id: str,
        request: RevisionDraftCreateRequest,
        idempotency_key: str,
        *,
        policy: Policy | None = None,
    ) -> tuple[RevisionDraft, bool]:
        auth = await self._authorize(session, policy)
        tenant, owner = auth.tenant_id, auth.subject_id
        identity = revision_draft_id(tenant, owner, agent_id, idempotency_key)
        digest = request.request_digest(agent_id)
        # Authority over the target first, even for a replay.
        document, holders = await self._target(auth, agent_id)
        existing = await self._drafts.revision(tenant, identity, 1)
        if existing is not None:
            return await self._replay(existing, owner, agent_id, digest, tenant, identity)
        predecessor = await self._predecessor(tenant, agent_id, document, holders)
        candidate = (
            request.candidate.model_dump(mode="json")
            if request.candidate is not None
            else _current_candidate(document)
        )
        now = self._clock()
        first = RevisionDraft.build(
            draft_id=identity,
            revision=1,
            tenant_id=tenant,
            owner_subject_id=owner,
            agent_id=agent_id,
            predecessor=predecessor,
            candidate=candidate,
            request_digest=digest,
            parent_digest=None,
            authorization=auth.authorization,
            created_at=now,
            committed_at=now,
        )
        stored, created = await self._drafts.commit(first)
        if created:
            return stored, True
        return await self._replay(stored, owner, agent_id, digest, tenant, identity)

    async def _replay(
        self,
        stored: RevisionDraft,
        owner: str,
        agent_id: str,
        digest: str,
        tenant: str,
        identity: str,
    ) -> tuple[RevisionDraft, bool]:
        if (stored.owner_subject_id, stored.agent_id, stored.request_digest) != (
            owner,
            agent_id,
            digest,
        ):
            raise RevisionDraftIdempotencyConflict("Idempotency key already used for another draft")
        current = await self._drafts.current(tenant, identity)
        if current is None:
            raise RevisionDraftUnavailable("Draft write outcome is unknown")
        return current, False

    async def _owned(self, auth: RevisionAuthority, agent_id: str, draft_id: str) -> RevisionDraft:
        current = await self._drafts.current(auth.tenant_id, draft_id)
        if current is None or (current.owner_subject_id, current.agent_id) != (
            auth.subject_id,
            agent_id,
        ):
            raise RevisionDraftNotFound("Draft not found")
        return current

    async def get_draft(
        self, session: Any, agent_id: str, draft_id: str, *, policy: Policy | None = None
    ) -> RevisionDraft:
        auth = await self._authorize(session, policy)
        await self._target(auth, agent_id)
        return await self._owned(auth, agent_id, draft_id)

    async def update_draft(
        self,
        session: Any,
        agent_id: str,
        draft_id: str,
        request: RevisionDraftUpdateRequest,
        *,
        policy: Policy | None = None,
    ) -> RevisionDraft:
        """Compare-and-set: the edit applies only to the revision the human edited."""
        auth = await self._authorize(session, policy)
        await self._target(auth, agent_id)
        current = await self._owned(auth, agent_id, draft_id)
        candidate = request.candidate.model_dump(mode="json")
        if request.expected_revision != current.revision:
            if request.expected_revision == current.revision - 1 and current.candidate == candidate:
                return current  # The identical edit already committed (retry).
            raise RevisionDraftConflict(current.revision)
        following = RevisionDraft.build(
            **{
                **current.model_dump(exclude={"state_digest", "candidate_digest"}),
                "revision": current.revision + 1,
                "candidate": candidate,
                "parent_digest": current.state_digest,
                "authorization": auth.authorization,
                "committed_at": max(self._clock(), current.committed_at),
            }
        )
        stored, created = await self._drafts.commit(following)
        if created or (stored.parent_digest, stored.candidate) == (current.state_digest, candidate):
            return stored
        raise RevisionDraftConflict(stored.revision)

    # --- Review ---------------------------------------------------------------------------

    async def review(
        self,
        session: Any,
        agent_id: str,
        draft_id: str,
        expected_revision: int,
        *,
        policy: Policy | None = None,
    ) -> RevisionReview:
        auth = await self._authorize(session, policy)
        tenant = auth.tenant_id
        document, holders = await self._target(auth, agent_id)
        draft = await self._owned(auth, agent_id, draft_id)
        if draft.revision != expected_revision:
            raise RevisionDraftConflict(draft.revision)
        predecessor = await self._predecessor(tenant, agent_id, document, holders)
        if predecessor != draft.predecessor:
            raise RevisionReviewNotCurrent("stale_predecessor")

        async def target_unchanged() -> None:
            current_doc, current_holders = await self._target(auth, agent_id)
            if (
                await self._predecessor(tenant, agent_id, current_doc, current_holders)
                != predecessor
            ):
                raise RevisionReviewNotCurrent("stale_predecessor")

        validated_at = self._clock()
        validation, composition = await self._gateway.validate(
            auth, draft.candidate, target_unchanged
        )
        requester = await self._gateway.recheck(auth)

        issues: list[str] = []
        if draft.candidate.get("type") != document.get("type"):
            issues.append("revision.type_immutable")  # Even when composition also fails.
        merged: dict[str, Any] | None = None
        candidate_definition: dict[str, Any] | None = None
        candidate_digest: str | None = None
        if composition.composed and composition.candidate is not None:
            merged = {
                **document,
                **{field: composition.candidate[field] for field in COMPOSED_FIELDS},
            }
            try:
                candidate_definition = canonical_definition(merged)
                candidate_digest = definition_digest(tenant, agent_id, candidate_definition)
            except CanonicalizationError:
                issues.append("revision.definition_invalid")
            issues.extend(self._revision_issues(agent_id, tenant, document, merged))
            if candidate_digest == predecessor.definition_digest:
                issues.append("revision.no_change")
        reasons = sorted(
            {issue.code for issue in validation.issues}
            | {issue.code for issue in composition.issues}
            | set(issues)
            | (
                set()
                if agent_access.can_write(requester, Agent.model_validate(document))
                else {"requester.cannot_write"}
            )
        )
        eligible = bool(
            validation.valid and composition.composed and candidate_digest and not reasons
        )
        changed: list[str] = []
        if candidate_definition is not None:
            before = canonical_definition(document)
            changed = sorted(
                f for f in COMPOSED_FIELDS if candidate_definition.get(f) != before.get(f)
            )
        snapshot = {
            "contract": "caos.agent_definition_revision_review",
            "schema_version": 1,
            "tenant_id": tenant,
            "requester": {
                "subject_id": requester.id,
                "roles": sorted(set(requester.roles)),
                "scopes": sorted(set(requester.scopes)),
            },
            "agent_id": agent_id,
            "executor_id": auth.binding.executor_id,
            "gateway_fingerprint": auth.binding.fingerprint(),
            "capability": REVISION_CAPABILITY,
            "draft": {
                "draft_id": draft.draft_id,
                "revision": draft.revision,
                "candidate_digest": draft.candidate_digest,
            },
            "predecessor": predecessor.model_dump(mode="json"),
            "normalized": validation.normalized,
            "normalized_digest": validation.normalized_digest,
            "candidate_definition": candidate_definition,
            "candidate_definition_digest": candidate_digest,
            "changed_fields": changed,
            "resolved": validation.resolved,
            "issues": reasons,
            "eligible": eligible,
            "policy": {
                "utu_policy_revision": auth.permit.versions[0],
                "composition": _policy(CURRENT_V2_POLICY),
            },
        }
        # JSON-normalized (tuples become lists) so the stored snapshot is identical.
        snapshot = json.loads(json.dumps(snapshot))
        snapshot_digest = sha256_hex(snapshot)
        digest = snapshot_digest if eligible else None
        expires_at = validated_at + REVIEW_TTL
        identity = review_id(digest, snapshot_digest, validated_at)
        envelope = _envelope(
            identity,
            agent_id,
            draft,
            predecessor,
            validation,
            composition,
            reasons,
            eligible,
            digest,
            changed,
            validated_at,
            expires_at,
        )
        return await self._reviews.insert(
            RevisionReview(
                review_id=identity,
                tenant_id=tenant,
                subject_id=auth.subject_id,
                agent_id=agent_id,
                executor_id=auth.binding.executor_id,
                draft_id=draft.draft_id,
                draft_revision=draft.revision,
                predecessor_revision_digest=predecessor.revision_digest,
                candidate_definition_digest=candidate_digest,
                eligible=eligible,
                review_digest=digest,
                snapshot_digest=snapshot_digest,
                snapshot=snapshot,
                envelope=envelope,
                authorization=auth.authorization,
                validated_at=validated_at,
                expires_at=expires_at,
            )
        )

    def _revision_issues(
        self,
        agent_id: str,
        tenant: str,
        document: Mapping[str, Any],
        merged: Mapping[str, Any],
    ) -> list[str]:
        issues = []
        for pin in self._provisions.values():
            if (pin.tenant_id, pin.agent_id) != (tenant, agent_id):
                continue
            pinned = {"name": pin.name, "type": pin.type, "role_name": pin.role_name}
            pinned["personality_key"] = pin.personality_key
            if any(value is not None and merged.get(k) != value for k, value in pinned.items()):
                issues.append("revision.pinned_identity")
        composition = self._conversation(merged)
        presents = any(
            (component or {}).get("type") == "chat"
            for component in merged.get("gui_components") or []
        )
        if composition.source == "tool" and not composition.available:
            issues.append(f"revision.conversation_{composition.reason or 'invalid'}")
        elif presents and composition.source != "tool":
            # New reviews require an explicit, valid conversation@1.0.0 for Conversation.
            issues.append("revision.conversation_required")
        return issues

    async def review_status(
        self, session: Any, agent_id: str, review_id_: str, *, policy: Policy | None = None
    ) -> dict[str, Any]:
        """The stored envelope and whether the review is still current (read-only)."""
        auth = await self._authorize(session, policy)
        review, state = await self._state(auth, agent_id, review_id_)
        return {**review.envelope, "state": state}

    async def require_current(
        self, session: Any, agent_id: str, review_id_: str, *, policy: Policy | None = None
    ) -> RevisionReview:
        """For a later approval step (REV-3): the review, only if it is current."""
        auth = await self._authorize(session, policy)
        review, state = await self._state(auth, agent_id, review_id_)
        if state != "current":
            raise RevisionReviewNotCurrent(state)
        return review

    async def authorize(self, session: Any, policy: Policy | None = None) -> RevisionAuthority:
        """The verified human reviser (fresh gateway authority and route policy)."""
        return await self._authorize(session, policy)

    async def recheck(self, auth: RevisionAuthority) -> None:
        await self._gateway.recheck(auth)

    async def current_review(
        self, auth: RevisionAuthority, agent_id: str, review_id_: str
    ) -> RevisionReview:
        """The requester's review of their own Agent, only if it is current."""
        review, state = await self._state(auth, agent_id, review_id_)
        if state != "current":
            raise RevisionReviewNotCurrent(state)
        return review

    async def require_target(self, auth: RevisionAuthority, agent_id: str) -> None:
        """The requester still owns, reads and writes this governed Agent."""
        await self._target(auth, agent_id)

    async def current_predecessor(self, auth: RevisionAuthority, agent_id: str) -> PredecessorRef:
        document, holders = await self._target(auth, agent_id)
        return await self._predecessor(auth.tenant_id, agent_id, document, holders)

    async def _state(
        self, auth: RevisionAuthority, agent_id: str, review_id_: str
    ) -> tuple[RevisionReview, ReviewState]:
        tenant = auth.tenant_id
        document, holders = await self._target(auth, agent_id)
        review = await self._reviews.get(tenant, review_id_)
        if review is None or (review.subject_id, review.agent_id) != (auth.subject_id, agent_id):
            raise RevisionReviewNotFound("Review not found")
        if not review.eligible:
            return review, "ineligible"
        if self._clock() >= review.expires_at:
            return review, "expired"
        # Gateway bindings and the UTU policy revision must be the ones reviewed under.
        if (
            review.snapshot.get("gateway_fingerprint") != auth.binding.fingerprint()
            or review.executor_id != auth.binding.executor_id
            or (review.snapshot.get("policy") or {}).get("utu_policy_revision")
            != auth.permit.versions[0]
        ):
            return review, "stale_authority"
        draft = await self._drafts.current(tenant, review.draft_id)
        if draft is None or draft.revision != review.draft_revision:
            return review, "stale_candidate"
        try:
            predecessor = await self._predecessor(tenant, agent_id, document, holders)
        except RevisionBaselineUnavailable:
            return review, "stale_predecessor"
        if predecessor.revision_digest != review.predecessor_revision_digest:
            return review, "stale_predecessor"
        return review, "current"


def _current_candidate(document: Mapping[str, Any]) -> dict[str, Any]:
    """The current definition's revisable fields as a complete candidate."""
    return AgentCreationInput.model_validate(
        {
            field: document.get(field)
            for field in AgentCreationInput.model_fields
            if document.get(field) is not None
        }
    ).model_dump(mode="json")


def _envelope(
    identity: str,
    agent_id: str,
    draft: RevisionDraft,
    predecessor: PredecessorRef,
    validation: Any,
    composition: Any,
    reasons: list[str],
    eligible: bool,
    digest: str | None,
    changed: list[str],
    validated_at: datetime,
    expires_at: datetime,
) -> dict[str, Any]:
    """Visibility-safe projection, by the G3.3 rules: generic messages, no ``resolved``
    dependencies, no backend selectors, no internal identities beyond the requester's."""
    safe_validation = validation.model_dump(mode="json")
    safe_validation["resolved"] = None
    for issue in safe_validation["issues"]:
        issue["message"] = "Configuration is invalid or unavailable for this requester"
    preview = None
    if composition.composed and composition.candidate is not None:
        preview = {key: composition.candidate[key] for key in PREVIEW_FIELDS}
    return {
        "contract": "caos.agent_definition_revision_review",
        "schema_version": 1,
        "review_id": identity,
        "agent_id": agent_id,
        "draft": {"draft_id": draft.draft_id, "revision": draft.revision},
        "predecessor": {
            "revision": predecessor.revision,
            "revision_digest": predecessor.revision_digest,
            "materialized": predecessor.materialized,
        },
        "validation": safe_validation,
        "composition": {
            "compatible": composition.composed,
            "issues": [
                {"path": issue.path, "code": issue.code, "message": "V2 composition is unavailable"}
                for issue in composition.issues
            ],
            "preview": preview,
        },
        "changed_fields": changed,
        "eligibility": {"eligible": eligible, "reasons": reasons},
        "review_digest": digest,
        "validated_at": validated_at.isoformat(),
        "expires_at": expires_at.isoformat(),
        "capability": REVISION_CAPABILITY,
        # A review never authorizes these; approval (REV-3) and application (REV-4) are
        # separate, not implemented.
        "authorizes": {"approval": False, "revision": False, "application": False},
    }
