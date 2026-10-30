"""Deterministic Manuscript runtime (MANUSCRIPT-0.1).

Starts sessions pinned to an exact Manuscript, evaluates typed answers (requester-scoped
catalog membership included), applies transitions, corrections, restarts and
completion, and emits a validated typed artifact. It calls no LLM and holds no Store,
approval, activation or draft-write handle: the artifact is data for a later, separately
authorized proposal (``agent.draft.propose@1.0.0``), which is declared, never invoked.

Callers pass a verified ``ManuscriptActor``. No HTTP route or CPBX signal exposes this
service yet (the proposed ``manuscript.session.*`` capability awaits approval).
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import ValidationError

from src.base.entities.user import User
from src.base.events.v2.contracts import EventPermissionError
from src.base.operations.v2.admission import sha256_hex
from src.domains.agentverse.agents.creation_drafts.models import AgentDraftCreateRequest
from src.domains.agentverse.manuscripts.catalog import ManuscriptCatalog
from src.domains.agentverse.manuscripts.models import FieldSpec, ManuscriptDefinition, evaluate
from src.domains.agentverse.manuscripts.registry import ManuscriptRegistry, ManuscriptUnavailable
from src.domains.agentverse.manuscripts.repository import MongoManuscriptSessionRepository
from src.domains.agentverse.manuscripts.sessions import (
    MAX_APPLIED,
    Answer,
    AnswerSource,
    AppliedRequest,
    ManuscriptNotAvailable,
    ManuscriptSession,
    PinnedManuscript,
    SessionClosed,
    SessionCorrupt,
    SessionIdempotencyConflict,
    SessionIncomplete,
    SessionInvalidRequest,
    SessionNotFound,
    SessionRevisionConflict,
)
from src.domains.agentverse.security.revocation import ExecutionPermit
from src.domains.agentverse.security.utu import UTUAuthority

_KEY = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
MAX_ANSWERS = 32


@dataclass(frozen=True)
class ManuscriptActor:
    """Verified caller: the human requester, and the Agent acting for them (if any).

    Tenant, owner and source Agent come only from trusted authority; ``requester`` is the
    human whose catalog readability scopes every reference. Never a permit.
    """

    tenant_id: str
    subject_id: str
    agent_id: str
    kind: Literal["human", "agent"]
    requester: User

    @classmethod
    async def from_permit(cls, authority: UTUAuthority, permit: ExecutionPermit) -> ManuscriptActor:
        """An Agent (Enki) in its human's live session, from a verified UTU permit."""
        if not isinstance(permit, ExecutionPermit) or permit.origin != "session":
            raise EventPermissionError("Manuscripts require an Agent in a live human session")
        await authority.check_permit(permit)
        requester = await authority.resource_requester(permit)
        key = permit.key
        return cls(key.tenant_id, key.user_id, key.agent_id, "agent", requester)


@dataclass(frozen=True)
class AnswerOutcome:
    """``accepted`` (all accepted, stage complete), ``needs_clarification`` (stage still
    missing required answers) or ``invalid`` (nothing acceptable). Codes only."""

    status: Literal["accepted", "needs_clarification", "invalid"]
    accepted: list[str]
    rejected: dict[str, str]
    missing: list[str]


def _key(value: str) -> str:
    if not isinstance(value, str) or not _KEY.fullmatch(value):
        raise SessionInvalidRequest("Invalid request key")
    return value


class ManuscriptService:
    def __init__(
        self,
        *,
        registry: ManuscriptRegistry,
        repository: MongoManuscriptSessionRepository,
        catalog: ManuscriptCatalog,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._registry = registry
        self._repository = repository
        self._catalog = catalog
        self._clock = clock

    # --- Start and read ---------------------------------------------------------------------

    async def start(
        self,
        actor: ManuscriptActor,
        manuscript: str,
        *,
        idempotency_key: str,
        draft_id: str | None = None,
        prefill: dict[str, Any] | None = None,
    ) -> tuple[ManuscriptSession, dict[str, str]]:
        """Pin the exact Manuscript and start (or replay) a session; prefill answers are
        validated like any answer. Returns the session and rejected prefill codes."""
        key = _key(idempotency_key)
        try:
            definition = self._registry.resolve(manuscript)
        except ManuscriptUnavailable as exc:
            raise ManuscriptNotAvailable("Manuscript is not registered") from exc
        prefill = dict(prefill or {})
        if not set(prefill) <= set(definition.prefill) or len(prefill) > MAX_ANSWERS:
            raise SessionInvalidRequest("Only declared fields can be prefilled")
        start_digest = sha256_hex(
            {"manuscript": definition.key(), "draft_id": draft_id, "prefill": prefill}
        )
        session_id = str(
            uuid5(
                NAMESPACE_URL,
                "caos:manuscript-session:"
                + sha256_hex(
                    {
                        "tenant_id": actor.tenant_id,
                        "subject_id": actor.subject_id,
                        "agent_id": actor.agent_id,
                        "key": key,
                    }
                ),
            )
        )
        existing = await self._repository.get(actor.tenant_id, session_id)
        if existing is not None:
            self._check_owner(existing, actor)
            if existing.start_digest != start_digest:
                raise SessionIdempotencyConflict("Idempotency key already started another session")
            return existing, {}
        accepted, rejected = await self._validate_all(definition, prefill, actor.requester)
        now = self._clock()
        answers = {
            field: Answer(value=value, source="prefill", revision=1)
            for field, value in accepted.items()
        }
        stage, completed = self._advance(definition, definition.initial_stage, [], answers)
        session = ManuscriptSession.build(
            session_id=session_id,
            tenant_id=actor.tenant_id,
            owner_subject_id=actor.subject_id,
            source_agent_id=actor.agent_id,
            manuscript=PinnedManuscript(
                id=definition.id, version=definition.version, fingerprint=definition.fingerprint()
            ),
            draft_id=draft_id,
            start_digest=start_digest,
            revision=1,
            status="active",
            stage=stage,
            completed_stages=completed,
            answers=answers,
            created_at=now,
            updated_at=now,
        )
        stored, created = await self._repository.insert(session)
        if not created and stored.start_digest != start_digest:
            raise SessionIdempotencyConflict("Idempotency key already started another session")
        return stored, rejected

    def definition(self, manuscript: str) -> ManuscriptDefinition:
        """The exact registered Manuscript (``id@version``) or ``manuscript.unavailable``."""
        try:
            return self._registry.resolve(manuscript)
        except ManuscriptUnavailable as exc:
            raise ManuscriptNotAvailable("Manuscript is not registered") from exc

    async def owned(
        self, actor: ManuscriptActor, session_id: str
    ) -> tuple[ManuscriptSession, ManuscriptDefinition]:
        """The actor's own session and its exactly pinned definition (a session ID is a
        reference only; another owner's, tenant's or Agent's session is not found)."""
        return await self._load(actor, session_id)

    async def status(self, actor: ManuscriptActor, session_id: str) -> dict[str, Any]:
        session, definition = await self._load(actor, session_id)
        return self.view(session, definition)

    def view(self, session: ManuscriptSession, definition: ManuscriptDefinition) -> dict[str, Any]:
        """Progress projection: no owner, tenant, provenance internals or guidance text."""
        values = session.values()
        stage = definition.stage(session.stage)
        return {
            "contract": "caos.manuscript_session",
            "schema_version": 1,
            "session_id": session.session_id,
            "manuscript": session.manuscript.model_dump(mode="json"),
            "revision": session.revision,
            "status": session.status,
            "stage": stage.key,
            "stage_title": stage.title,
            "guidance": stage.guidance,
            "completed_stages": list(session.completed_stages),
            "answers": values,
            "missing": self._missing(definition, stage.key, values),
            "allowed_transitions": [
                transition.to
                for transition in stage.transitions
                if evaluate(transition.when, values)
            ],
            "ready_to_complete": session.status == "active"
            and self._completable(definition, session),
            "draft_id": session.draft_id,
            "artifact": session.artifact,
            "restarted_from": session.restarted_from,
            "restarted_to": session.restarted_to,
            # A session, or its artifact, never authorizes anything.
            "authorizes": {
                "draft_write": False,
                "approval": False,
                "creation": False,
                "activation": False,
            },
        }

    # --- Transitions --------------------------------------------------------------------------

    async def answer(
        self,
        actor: ManuscriptActor,
        session_id: str,
        *,
        expected_revision: int,
        answers: dict[str, Any],
        request_key: str,
    ) -> tuple[ManuscriptSession, AnswerOutcome]:
        """Evaluate typed answers for the current or later stages (non-linear), then
        advance deterministically. Answers to completed stages require ``correct``."""
        if not answers or len(answers) > MAX_ANSWERS:
            raise SessionInvalidRequest("Answer one to 32 fields")
        digest = sha256_hex({"op": "answer", "expected": expected_revision, "answers": answers})

        async def transition(
            session: ManuscriptSession, definition: ManuscriptDefinition
        ) -> tuple[dict[str, Any], AnswerOutcome]:
            rejected: dict[str, str] = {}
            pending: dict[str, Any] = {}
            for field, value in answers.items():
                if field not in {spec.key for spec in definition.fields}:
                    rejected[field] = "field.unknown"
                elif definition.owner(field) in session.completed_stages:
                    rejected[field] = "stage.completed"
                else:
                    pending[field] = value
            accepted, invalid = await self._validate_all(definition, pending, actor.requester)
            rejected.update(invalid)
            if not accepted:
                values = session.values()
                return {}, AnswerOutcome(
                    "invalid", [], rejected, self._missing(definition, session.stage, values)
                )
            changes = self._answers(session, accepted, actor.kind)
            stage, completed = self._advance(
                definition, session.stage, list(session.completed_stages), changes
            )
            values = {key: answer.value for key, answer in changes.items()}
            # ``missing`` is what the (new) current stage still needs: the next question.
            return {
                "answers": changes,
                "stage": stage,
                "completed_stages": completed,
            }, AnswerOutcome(
                self._status(rejected, session.stage in completed),
                sorted(accepted),
                rejected,
                self._missing(definition, stage, values),
            )

        return await self._mutate(
            actor, session_id, expected_revision, request_key, digest, transition
        )

    async def correct(
        self,
        actor: ManuscriptActor,
        session_id: str,
        *,
        expected_revision: int,
        stage: str,
        answers: dict[str, Any] | None = None,
        request_key: str,
    ) -> tuple[ManuscriptSession, AnswerOutcome]:
        """Reopen a completed stage; it and every stage completed after it become
        incomplete (answers are kept), corrections are applied, then progression resumes."""
        answers = dict(answers or {})
        if len(answers) > MAX_ANSWERS:
            raise SessionInvalidRequest("Answer at most 32 fields")
        digest = sha256_hex(
            {"op": "correct", "expected": expected_revision, "stage": stage, "answers": answers}
        )

        async def transition(
            session: ManuscriptSession, definition: ManuscriptDefinition
        ) -> tuple[dict[str, Any], AnswerOutcome]:
            if stage not in session.completed_stages and stage != session.stage:
                raise SessionInvalidRequest(
                    "Only a completed or the current stage can be corrected"
                )
            completed = list(session.completed_stages)
            completed = completed[: completed.index(stage)] if stage in completed else completed
            rejected = {
                field: "field.not_in_stage"
                for field in answers
                if field not in definition.stage(stage).collects
            }
            accepted, invalid = await self._validate_all(
                definition,
                {field: value for field, value in answers.items() if field not in rejected},
                actor.requester,
            )
            rejected.update(invalid)
            changes = self._answers(session, accepted, actor.kind)
            current, completed = self._advance(definition, stage, completed, changes)
            values = {key: answer.value for key, answer in changes.items()}
            return {
                "answers": changes,
                "stage": current,
                "completed_stages": completed,
            }, AnswerOutcome(
                "invalid"
                if rejected and not accepted and answers
                else self._status(rejected, stage in completed),
                sorted(accepted),
                rejected,
                self._missing(definition, current, values),
            )

        return await self._mutate(
            actor, session_id, expected_revision, request_key, digest, transition
        )

    async def complete(
        self,
        actor: ManuscriptActor,
        session_id: str,
        *,
        expected_revision: int,
        request_key: str,
    ) -> ManuscriptSession:
        """Emit the typed artifact once the final stage and Manuscript completion hold."""
        digest = sha256_hex({"op": "complete", "expected": expected_revision})

        async def transition(
            session: ManuscriptSession, definition: ManuscriptDefinition
        ) -> tuple[dict[str, Any], None]:
            if not self._completable(definition, session):
                raise SessionIncomplete(self._all_missing(definition, session.values()))
            artifact = self.artifact(definition, session.values())
            return {
                "status": "completed",
                "artifact": artifact,
                "artifact_digest": sha256_hex(artifact),
            }, None

        session, _ = await self._mutate(
            actor, session_id, expected_revision, request_key, digest, transition
        )
        return session

    async def restart(
        self, actor: ManuscriptActor, session_id: str, *, request_key: str
    ) -> ManuscriptSession:
        """Start over on the same pinned Manuscript; the previous session stays readable."""
        _key(request_key)
        session, definition = await self._load(actor, session_id)
        successor_id = str(uuid5(NAMESPACE_URL, f"caos:manuscript-restart:{session.session_id}"))
        if session.status == "restarted":
            successor = await self._repository.get(actor.tenant_id, session.restarted_to or "")
            if successor is None:
                raise SessionCorrupt("Restarted session has no successor")
            return successor
        now = self._clock()
        stage, completed = self._advance(definition, definition.initial_stage, [], {})
        successor = ManuscriptSession.build(
            session_id=successor_id,
            tenant_id=session.tenant_id,
            owner_subject_id=session.owner_subject_id,
            source_agent_id=session.source_agent_id,
            manuscript=session.manuscript,
            draft_id=session.draft_id,
            start_digest=sha256_hex({"restart_of": session.session_id}),
            revision=1,
            status="active",
            stage=stage,
            completed_stages=completed,
            answers={},
            restarted_from=session.session_id,
            created_at=now,
            updated_at=now,
        )
        # The successor first (deterministic identity), then the predecessor's CAS: a
        # crash between them is completed by repeating the restart.
        successor, _ = await self._repository.insert(successor)
        closed = ManuscriptSession.build(
            **{
                **session.model_dump(exclude={"state_digest"}),
                "revision": session.revision + 1,
                "status": "restarted",
                "restarted_to": successor_id,
                "updated_at": max(now, session.updated_at),
            }
        )
        if not await self._repository.replace(session, closed):
            current = await self._repository.get(actor.tenant_id, session.session_id)
            if current is None or current.restarted_to != successor_id:
                raise SessionRevisionConflict(current.revision if current else session.revision)
        return successor

    # --- Internals ------------------------------------------------------------------------------

    async def _load(
        self, actor: ManuscriptActor, session_id: str
    ) -> tuple[ManuscriptSession, ManuscriptDefinition]:
        session = await self._repository.get(actor.tenant_id, session_id)
        if session is None:
            raise SessionNotFound("Manuscript session not found")
        self._check_owner(session, actor)
        try:
            definition = self._registry.resolve(
                session.manuscript.key(), session.manuscript.fingerprint
            )
        except ManuscriptUnavailable as exc:
            raise ManuscriptNotAvailable("The pinned Manuscript is not available") from exc
        fields = {spec.key for spec in definition.fields}
        stages = {stage.key for stage in definition.stages}
        if (
            session.stage not in stages
            or not set(session.completed_stages) <= stages
            or not set(session.answers) <= fields
        ):
            raise SessionCorrupt("Session state does not match its pinned Manuscript")
        return session, definition

    @staticmethod
    def _check_owner(session: ManuscriptSession, actor: ManuscriptActor) -> None:
        # Another owner's, tenant's or source Agent's session is indistinguishable.
        if (session.tenant_id, session.owner_subject_id, session.source_agent_id) != (
            actor.tenant_id,
            actor.subject_id,
            actor.agent_id,
        ):
            raise SessionNotFound("Manuscript session not found")

    async def _mutate(
        self,
        actor: ManuscriptActor,
        session_id: str,
        expected_revision: int,
        request_key: str,
        digest: str,
        transition: Callable[
            [ManuscriptSession, ManuscriptDefinition], Awaitable[tuple[dict[str, Any], Any]]
        ],
    ) -> tuple[ManuscriptSession, Any]:
        """One compare-and-set transition, retry-safe by request key."""
        key = _key(request_key)
        session, definition = await self._load(actor, session_id)
        if session.replay(key, digest):
            return session, None  # Already applied: the stored state is the result.
        if session.status != "active":
            raise SessionClosed(session.status)
        if expected_revision != session.revision:
            raise SessionRevisionConflict(session.revision)
        changes, outcome = await transition(session, definition)
        if not changes:
            return session, outcome  # Nothing acceptable: no revision is spent.
        revision = session.revision + 1
        applied = [*session.applied, AppliedRequest(key=key, digest=digest, revision=revision)]
        following = ManuscriptSession.build(
            **{
                **session.model_dump(exclude={"state_digest"}),
                **changes,
                "revision": revision,
                "applied": applied[-MAX_APPLIED:],
                "updated_at": max(self._clock(), session.updated_at),
            }
        )
        if await self._repository.replace(session, following):
            return following, outcome
        current = await self._repository.get(actor.tenant_id, session_id)
        if current is not None and current.replay(key, digest):
            return current, outcome  # An identical concurrent request applied it.
        raise SessionRevisionConflict(current.revision if current else session.revision)

    @staticmethod
    def _status(
        rejected: dict[str, str], stage_completed: bool
    ) -> Literal["accepted", "needs_clarification"]:
        """``accepted``: every answer accepted and the stage they were given in is now
        complete; otherwise the human must clarify (missing or rejected answers)."""
        return "accepted" if not rejected and stage_completed else "needs_clarification"

    def _answers(
        self, session: ManuscriptSession, accepted: dict[str, Any], kind: str
    ) -> dict[str, Answer]:
        source: AnswerSource = "agent" if kind == "agent" else "human"
        merged = dict(session.answers)
        for field, value in accepted.items():
            if value is None:
                merged.pop(field, None)  # An explicit clear of an optional answer.
            else:
                merged[field] = Answer(value=value, source=source, revision=session.revision + 1)
        return merged

    async def _validate_all(
        self, definition: ManuscriptDefinition, answers: dict[str, Any], requester: User
    ) -> tuple[dict[str, Any], dict[str, str]]:
        accepted: dict[str, Any] = {}
        rejected: dict[str, str] = {}
        for field, value in answers.items():
            normalized, code = await self._validate(definition.field(field), value, requester)
            if code is None:
                accepted[field] = normalized
            else:
                rejected[field] = code
        return accepted, rejected

    async def _validate(
        self, spec: FieldSpec, value: Any, requester: User
    ) -> tuple[Any, str | None]:
        """Typed validation; catalog fields resolve against the requester's catalog."""
        if value is None:
            return None, None
        if spec.type == "boolean":
            return (value, None) if isinstance(value, bool) else (None, "answer.type")
        if spec.type in ("text", "enum", "catalog_ref"):
            if not isinstance(value, str) or not value.strip():
                return None, "answer.type"
            text = re.sub(r"\s+", " ", value.strip()) if spec.type == "text" else value.strip()
            if spec.type == "enum":
                return (text, None) if text in (spec.options or []) else (None, "answer.option")
            if spec.type == "catalog_ref":
                key = await self._catalog.resolve(spec.catalog or "", text, requester)
                return (key, None) if key is not None else (None, "reference.unavailable")
            return self._text(spec, text)
        if not isinstance(value, list) or not all(
            isinstance(item, str) and item.strip() for item in value
        ):
            return None, "answer.type"
        items = [item.strip() for item in value]
        if len(set(items)) != len(items):
            return None, "answer.duplicate"
        if spec.max_items is not None and len(items) > spec.max_items:
            return None, "answer.too_many"
        if spec.type == "text_list":
            for item in items:
                if self._text(spec, item)[1] is not None:
                    return None, "answer.invalid"
            return items, None
        keys = []
        for item in items:
            key = await self._catalog.resolve(spec.catalog or "", item, requester)
            if key is None:
                return None, "reference.unavailable"
            keys.append(key)
        return keys, None

    @staticmethod
    def _text(spec: FieldSpec, text: str) -> tuple[Any, str | None]:
        if spec.min_length is not None and len(text) < spec.min_length:
            return None, "answer.too_short"
        if spec.max_length is not None and len(text) > spec.max_length:
            return None, "answer.too_long"
        if spec.pattern is not None and not re.fullmatch(spec.pattern, text):
            return None, "answer.invalid"
        return text, None

    @staticmethod
    def _stage_complete(
        definition: ManuscriptDefinition, stage: str, values: dict[str, Any]
    ) -> bool:
        spec = definition.stage(stage)
        present = all(values.get(field) not in (None, "", []) for field in spec.required)
        return present and (spec.completion is None or evaluate(spec.completion, values))

    def _advance(
        self,
        definition: ManuscriptDefinition,
        stage: str,
        completed: list[str],
        answers: dict[str, Answer],
    ) -> tuple[str, list[str]]:
        """Complete satisfied stages and follow the first matching transition; each
        stage is visited at most once per call, so progression is bounded."""
        values = {key: answer.value for key, answer in answers.items()}
        completed = list(completed)
        visited: set[str] = set()
        while stage not in visited:
            visited.add(stage)
            if not self._stage_complete(definition, stage, values):
                break
            if stage not in completed:
                completed.append(stage)
            following = next(
                (t.to for t in definition.stage(stage).transitions if evaluate(t.when, values)),
                None,
            )
            if following is None:
                break
            stage = following
        return stage, completed

    def _missing(
        self, definition: ManuscriptDefinition, stage: str, values: dict[str, Any]
    ) -> list[str]:
        return [
            field
            for field in definition.stage(stage).required
            if values.get(field) in (None, "", [])
        ]

    def _all_missing(self, definition: ManuscriptDefinition, values: dict[str, Any]) -> list[str]:
        return sorted(
            field
            for stage in definition.stages
            for field in stage.required
            if values.get(field) in (None, "", [])
        )

    def _completable(self, definition: ManuscriptDefinition, session: ManuscriptSession) -> bool:
        values = session.values()
        return (
            session.stage == definition.final_stage
            and definition.final_stage in session.completed_stages
            and not self._all_missing(definition, values)
            and evaluate(definition.completion, values)
        )

    @staticmethod
    def prefill_from_creation(
        definition: ManuscriptDefinition, creation: dict[str, Any]
    ) -> dict[str, Any]:
        """Map draft ``creation`` content onto prefillable answers (the artifact's
        inverse). The result is still validated by ``start``; nothing is trusted."""
        prefill: dict[str, Any] = {}
        for spec in definition.fields:
            name = spec.target.removeprefix("/creation/") if spec.target else None
            if spec.key not in definition.prefill or name is None or name not in creation:
                continue
            value = creation[name]
            if spec.shape == "tool_refs" and isinstance(value, list):
                value = [item.get("key") if isinstance(item, dict) else item for item in value]
            prefill[spec.key] = value
        return prefill

    @staticmethod
    def artifact(definition: ManuscriptDefinition, values: dict[str, Any]) -> dict[str, Any]:
        """The typed result, validated against the existing draft contract."""
        creation: dict[str, Any] = {}
        for spec in definition.fields:
            if spec.target is None or values.get(spec.key) in (None, "", []):
                continue
            value = values[spec.key]
            if spec.shape == "tool_refs":
                value = [{"key": key, "config": {}} for key in value]
            creation[spec.target.removeprefix("/creation/")] = value
        artifact = {**definition.artifact.model_dump(mode="json"), "creation": creation}
        try:
            AgentDraftCreateRequest.model_validate(artifact)
        except ValidationError as exc:
            raise SessionInvalidRequest("Artifact does not satisfy the draft contract") from exc
        return artifact
