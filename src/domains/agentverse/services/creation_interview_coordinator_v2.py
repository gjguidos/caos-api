"""Creation interview coordinator (ENKI-1.0-B / COORD-0.1).

Deterministic application service, invoked by the V2 conversation dispatcher inside a
turn's CONV-REC-0.1 lease for an explicit ``activity.start`` / ``activity.close`` event.
It owns no state, store, grant, signal or trusted line, and calls no model:

- the **Activity Context** service links the conversation to its Manuscript session and
  the human's draft (and records the Seshat ``activity.context.*`` evidence);
- the **Manuscript** runtime, reached only through its scoped trusted helper on the
  acting Agent's attenuated principal, decides progress and the next fields;
- the acting Agent's **composition** decides availability: its Conversation Tool must
  declare the exact activity and its Manuscript Tool attachment names the Manuscript.

It never creates or writes a draft, submits or decides a proposal, approves, creates or
activates an Agent. No branch depends on a particular Agent's identity.

COORD-0.2 adds ``activity.answer``: the human's message is interpreted by the governed
Reasoning capability (``cognitive.interpret``, attached through the Agent's Reasoning
Tool) into *candidates* for the Manuscript's current stage only. The coordinator filters
them deterministically and Manuscript validates and applies them; a model never writes a
session or draft, and boolean confirmations are never inferred. Trusted in-process calls
on the Agent's session principal: Enki's runtime is not awakened.

Idempotency: keys are derived, never chosen by the client. A start is keyed by
``(conversation, activity, draft, generation)`` so that a retry under a *new* event ID
(the journal settles an interrupted turn as ``interrupted``) converges on the same
Manuscript session and context instead of creating second ones. A close is keyed by the
turn's event ID; a closed context with the requested reason is reported, not reclosed.
An answer is bound to the Manuscript revision the human answered: one request key per
``(context, session, revision)`` and an expected-revision compare-and-set, so it is applied
at most once and never re-interpreted against a later stage (a moved revision is
reported ``stale``, without interpretation or mutation).
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Final

from pydantic import ValidationError

from src.base.events.v2.contracts import EventPermissionError
from src.base.operations.v2.admission import canonical_json, sha256_hex
from src.domains.agentverse.activities.models import (
    ActivityActiveExists,
    ActivityClosed,
    ActivityContext,
    ActivityNotFound,
    OwnerRef,
)
from src.domains.agentverse.activities.owners import CONVERSATION_OWNER, TURN_SIGNAL
from src.domains.agentverse.activities.service import ActivityActor, ActivityContextService
from src.domains.agentverse.agents.conversational_composition_v2 import (
    ConversationGate,
    DefinitionBinding,
)
from src.domains.agentverse.agents.lifecycle.agent_principal_resolver import VerifiedAgentSession
from src.domains.agentverse.catalog.capability import SIGNAL as CATALOG_SIGNAL
from src.domains.agentverse.catalog.capability import project_catalog
from src.domains.agentverse.entities.conversation_v2 import (
    ActivityAnswerV2,
    ActivityCloseV2,
    ActivityStartV2,
    ClientEventV2,
)
from src.domains.agentverse.manuscripts.capability import SIGNALS as MANUSCRIPT_SIGNALS
from src.domains.agentverse.manuscripts.capability import invoke_manuscript
from src.domains.agentverse.manuscripts.catalog import CATALOG_KINDS
from src.domains.agentverse.manuscripts.models import FieldSpec, ManuscriptDefinition, Stage
from src.domains.agentverse.manuscripts.service import ManuscriptService
from src.domains.agentverse.manuscripts.sessions import ManuscriptError
from src.domains.agentverse.ncsc.cpbx import SynapticExchange
from src.domains.agentverse.reasoning.capability import SIGNAL as REASONING_SIGNAL
from src.domains.agentverse.reasoning.tasks.manuscript_answer_extraction import (
    MANUSCRIPT_ANSWER_EXTRACTION,
    MAX_GUIDANCE,
    MAX_MESSAGE,
    MAX_TEXT_VALUE,
)
from src.domains.agentverse.registries import tool_registry_instance
from src.domains.agentverse.registries.tool_registry import ToolRegistry
from src.domains.agentverse.repositories.conversation_v2 import TurnLease, TurnOwnershipLost
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey
from src.domains.agentverse.security.utu import AuthorizedAgentPrincipal, UTUAuthority
from src.domains.agentverse.services.activity_errors_v2 import CoordinatorError
from src.domains.agentverse.tools.manuscript_tool.spec import ManuscriptToolConfig, manuscript_tool
from src.domains.agentverse.tools.reasoning_tool import ReasoningTool
from src.domains.agentverse.tools.reasoning_tool.spec import ReasoningToolConfig, reasoning_tool
from src.domains.agentverse.tools.spec import AgentToolRef

__all__ = ["CoordinatorError", "CreationInterviewCoordinator", "prompt"]

INTERVIEW: Final = "agent.creation_interview@1.0.0"
OUTCOME_CONTRACT: Final = "caos.activity_turn"
DRAFT_ARTIFACT: Final = "caos.agent_creation_draft"
MANUSCRIPT_TOOL: Final = "manuscript"
# Exactly what each step reaches; checked before any mutation, re-checked by providers.
START_GRANTS: Final = frozenset(
    {TURN_SIGNAL, MANUSCRIPT_SIGNALS["start"], MANUSCRIPT_SIGNALS["status"]}
)
CLOSE_GRANTS: Final = frozenset({TURN_SIGNAL})
ANSWER_GRANTS: Final = frozenset(
    {
        TURN_SIGNAL,
        MANUSCRIPT_SIGNALS["status"],
        MANUSCRIPT_SIGNALS["answer"],
        REASONING_SIGNAL,
    }
)
REASONING_TOOL: Final = "reasoning"
ANSWER_TASK: Final = MANUSCRIPT_ANSWER_EXTRACTION.key()
# One model call's share of the answer turn (the dispatcher bounds the whole turn).
REASONING_SECONDS: Final = 30.0
# Published task limits (``manuscript.answer_extraction@1.0.0``).
MAX_OPTIONS: Final = 64
MAX_OPTION_CHARS: Final = 100
MAX_CATALOG_OPTIONS: Final = 50
MAX_TASK_FIELDS: Final = 16
MAX_LIST_ITEMS: Final = 32
MAX_LABEL: Final = 120
_NOT_AUTHORIZED: Final = {
    "draft_write": False,
    "approval": False,
    "creation": False,
    "activation": False,
}


def _key(prefix: str, **parts: Any) -> str:
    """A derived ``[A-Za-z0-9_-]{16,128}`` key (Manuscript and Activity formats)."""
    return prefix + "-" + sha256_hex({"contract": "caos.coordinator_key", "v": 1, **parts})


class CreationInterviewCoordinator:
    activity = INTERVIEW

    def __init__(
        self,
        *,
        authority: UTUAuthority,
        exchange: SynapticExchange,
        contexts: ActivityContextService,
        manuscripts: ManuscriptService,
        gate: ConversationGate,
        turns: Callable[[TurnLease], Awaitable[bool]],
        tools: ToolRegistry = tool_registry_instance,
    ) -> None:
        self._authority = authority
        self._exchange = exchange
        self._contexts = contexts
        self._manuscripts = manuscripts
        self._gate = gate
        # Read-only CONV-REC-0.1 ownership check (``ConversationRepositoryV2.owns``).
        self._turns = turns
        self._tools = tools

    # --- Turn entry points (called under the turn's live lease) ------------------------------

    async def start(
        self, session: VerifiedAgentSession, event: ClientEventV2, lease: TurnLease
    ) -> tuple[dict[str, Any], DefinitionBinding]:
        request = event.activity
        assert isinstance(request, ActivityStartV2) and request.key == self.activity
        agent_id = event.agent_ids[0]
        principal, actor = await self._actor(session, agent_id, START_GRANTS)
        definition, binding = await self._gate.admit_activity(
            session.tenant_id, agent_id, self.activity
        )
        manuscript = self._manuscript(definition)
        owner = OwnerRef(kind=CONVERSATION_OWNER, id=event.conversation_id)
        draft_id = request.references.draft
        context = await self._resume(actor, owner, draft_id)
        resumed = context is not None
        if context is None:
            generation = await self._next_generation(actor, owner)
            seed = {
                "conversation_id": event.conversation_id,
                "activity": self.activity,
                "draft_id": draft_id,
                "generation": generation,
            }
            await self._gate.ensure_activity_current(
                session.tenant_id, agent_id, binding, self.activity
            )
            started = await self._invoke(
                principal,
                "start",
                {
                    "contract": "caos.manuscript_session.start",
                    "schema_version": 1,
                    "manuscript": manuscript.key(),
                    "idempotency_key": _key("coord-mss", **seed),
                    "draft_id": draft_id,
                },
            )
            try:
                context, _ = await self._contexts.start(
                    actor,
                    lease,
                    owner=owner,
                    activity=self.activity,
                    references={
                        "manuscript_session": started["session"]["session_id"],
                        "draft": draft_id,
                    },
                    operation_id=_key("coord-act", **seed),
                )
            except ActivityActiveExists:
                # Decided concurrently for this draft (else still refused).
                context = await self._resume(actor, owner, draft_id)
                if context is None:
                    raise
                resumed = True
        return await self._outcome(principal, context, manuscript, resumed=resumed), binding

    async def close(
        self, session: VerifiedAgentSession, event: ClientEventV2, lease: TurnLease
    ) -> dict[str, Any]:
        request = event.activity
        assert isinstance(request, ActivityCloseV2)
        agent_id = event.agent_ids[0]
        _, actor = await self._actor(session, agent_id, CLOSE_GRANTS)
        owner = OwnerRef(kind=CONVERSATION_OWNER, id=event.conversation_id)
        context = await self._contexts.get(actor, request.context_id)
        if context.owner != owner or context.activity.key != self.activity:
            # Another conversation's (or activity's) context is indistinguishable from none.
            raise ActivityNotFound("Activity context not found")
        already = context.status == "closed"
        if already:
            if context.closed_reason != request.reason:
                raise ActivityClosed("The activity context is already closed")
        else:
            context = await self._contexts.close(
                actor,
                lease,
                context_id=context.context_id,
                expected_revision=context.revision,
                operation_id=_key(
                    "coord-close",
                    conversation_id=event.conversation_id,
                    event_id=event.event_id,
                ),
                reason=request.reason,
            )
        return self._envelope(context, resumed=already, manuscript=None, prompt=None)

    async def answer(
        self, session: VerifiedAgentSession, event: ClientEventV2, lease: TurnLease
    ) -> tuple[dict[str, Any], DefinitionBinding]:
        """Interpret one human answer for the context's current Manuscript stage.

        Manuscript decides the stage, fields and validity; Reasoning proposes candidates
        for that stage only; nothing is written unless a filtered candidate survives, and
        then only through Manuscript's expected-revision ``answer``.
        """
        request = event.activity
        assert isinstance(request, ActivityAnswerV2) and event.message is not None
        agent_id = event.agent_ids[0]
        principal, actor = await self._actor(session, agent_id, ANSWER_GRANTS)
        definition, binding = await self._gate.admit_activity(
            session.tenant_id, agent_id, self.activity
        )
        manuscript = self._manuscript(definition)
        reasoning = self._reasoning(definition)
        context = await self._owned(actor, event, request.context_id)
        view = await self._view(principal, context, manuscript)
        if view["status"] != "active":
            raise CoordinatorError("activity.session_inactive")
        expected = request.expected_revision
        if view["revision"] != expected:
            # Answered against another revision (e.g. a retry after an interrupted commit):
            # never re-interpreted against the session's current stage.
            return self._result(context, view, manuscript, _interpretation("stale")), binding

        stage = manuscript.stage(view["stage"])
        specs = [manuscript.field(key) for key in stage.collects]
        confirmations = [spec.key for spec in specs if spec.type == "boolean"]
        offered = [spec for spec in specs if spec.type != "boolean" and _supported(spec)]
        unsupported = [s.key for s in specs if s.type != "boolean" and not _supported(s)]
        if not offered:
            # Confirmations are explicit human decisions, never inferred from a message.
            status = "confirmation_required" if confirmations else "input_unsupported"
            interpretation = _interpretation(
                status, confirmation_required=confirmations, unsupported=unsupported
            )
            return self._result(context, view, manuscript, interpretation), binding
        if len(event.message) > MAX_MESSAGE:
            interpretation = _interpretation(
                "input_unsupported", issues=["answer.message_too_long"]
            )
            return self._result(context, view, manuscript, interpretation), binding

        catalogued = [spec for spec in offered if spec.catalog is not None]
        if catalogued and CATALOG_SIGNAL not in self._authority.grants_for(principal).invoke:
            raise EventPermissionError("Activity grants denied")
        options, incomplete = await self._catalog(principal, catalogued)
        bounded = _task_input(event.message, manuscript, stage, offered, view, options)
        if bounded is None:
            interpretation = _interpretation("input_unsupported", issues=["answer.input_too_large"])
            return self._result(context, view, manuscript, interpretation), binding
        task_input, trimmed = bounded
        incomplete |= trimmed

        await self._gate.ensure_activity_current(
            session.tenant_id, agent_id, binding, self.activity
        )
        await self._live(lease)
        result = await reasoning.invoke(
            authority=self._authority,
            exchange=self._exchange,
            principal=principal,
            request={
                "contract": "caos.reasoning.request",
                "schema_version": 1,
                "task": ANSWER_TASK,
                "request_key": _key(
                    "coord-rsn",
                    context_id=context.context_id,
                    revision=expected,
                    event_id=event.event_id,
                ),
                "input": task_input,
            },
            deadline=time.monotonic() + REASONING_SECONDS,
        )
        accepted, interpretation = _filter(result, offered, options, incomplete)
        interpretation["confirmation_required"] = confirmations
        interpretation["unsupported"] = unsupported
        if not accepted:
            return self._result(context, view, manuscript, interpretation), binding

        # Before the only mutation: the turn still owns the conversation, the human and
        # Agent still hold authority, the context is still active and the definition is
        # the admitted one. Manuscript then validates and compare-and-sets the revision.
        await self._live(lease)
        principal, actor = await self._actor(session, agent_id, ANSWER_GRANTS)
        context = await self._owned(actor, event, request.context_id)
        await self._gate.ensure_activity_current(
            session.tenant_id, agent_id, binding, self.activity
        )
        try:
            applied = await self._invoke(
                principal,
                "answer",
                {
                    "contract": "caos.manuscript_session.answer",
                    "schema_version": 1,
                    "session_id": view["session_id"],
                    "expected_revision": expected,
                    "answers": accepted,
                    "request_key": _key(
                        "coord-ans",
                        context_id=context.context_id,
                        session_id=view["session_id"],
                        revision=expected,
                    ),
                },
            )
        except CoordinatorError as exc:
            if exc.code not in ("session.revision_conflict", "idempotency.conflict"):
                raise
            # Another request owns this revision: report the current state, unchanged.
            view = await self._view(principal, context, manuscript)
            return self._result(context, view, manuscript, _interpretation("stale")), binding
        outcome = applied.get("outcome")
        if outcome is None:
            # Manuscript replayed this exact request: its stored state is the result.
            interpretation.update(status="applied", applied=sorted(accepted))
        else:
            interpretation["rejected"].update(outcome["rejected"])
            interpretation["applied"] = sorted(outcome["accepted"])
            interpretation["status"] = "applied" if outcome["accepted"] else "clarification"
        view = self._pinned(applied["session"], manuscript)
        return self._result(context, view, manuscript, interpretation), binding

    # --- Authority and configuration ----------------------------------------------------------

    async def _actor(
        self, session: VerifiedAgentSession, agent_id: str, required: frozenset[str]
    ) -> tuple[AuthorizedAgentPrincipal, ActivityActor]:
        """The acting Agent for this human's live session, resolved only through UTU."""
        principal = await self._authority.resolve_agent_principal(
            AgentInstanceKey(session.tenant_id, session.user_id, agent_id, session.session_id)
        )
        if not required <= set(self._authority.grants_for(principal).invoke):
            raise EventPermissionError("Activity grants denied")
        actor = await ActivityActor.from_principal(self._authority, principal)
        if (actor.tenant_id, actor.subject_id, actor.session_id) != (
            session.tenant_id,
            session.user_id,
            session.session_id,
        ):
            raise EventPermissionError("Activity actor mismatch")
        return principal, actor

    def _manuscript(self, definition: Mapping[str, Any]) -> ManuscriptDefinition:
        """The Manuscript named by the Agent's own Manuscript Tool attachment. No
        attachment (or an invalid one) is unconfigured: there is no implicit default."""
        attached = [
            ref
            for ref in (AgentToolRef.model_validate(item) for item in definition.get("tools") or [])
            if ref.key == MANUSCRIPT_TOOL
        ]
        item = self._tools.get_info(MANUSCRIPT_TOOL)
        if len(attached) != 1 or item is None or item.version != manuscript_tool.version:
            raise CoordinatorError("activity.manuscript_unconfigured")
        try:
            config = ManuscriptToolConfig.model_validate(attached[0].config)
            manuscript = self._manuscripts.definition(config.default_manuscript)
        except (ValidationError, ManuscriptError) as exc:
            raise CoordinatorError("activity.manuscript_unconfigured") from exc
        if manuscript.artifact.contract != DRAFT_ARTIFACT:
            raise CoordinatorError("activity.manuscript_unconfigured")
        return manuscript

    def _reasoning(self, definition: Mapping[str, Any]) -> ReasoningTool:
        """The Agent's own Reasoning Tool attachment, explicitly listing the answer task.

        Exactly one attachment of the registered Tool version, with an explicit ``tasks``
        configuration (the Tool's default task list is never assumed) that resolves to
        published tasks and includes ``manuscript.answer_extraction@1.0.0``."""
        attached = [
            ref
            for ref in (AgentToolRef.model_validate(item) for item in definition.get("tools") or [])
            if ref.key == REASONING_TOOL
        ]
        item = self._tools.get_info(REASONING_TOOL)
        if (
            len(attached) != 1
            or item is None
            or item.version != reasoning_tool.version
            or "tasks" not in attached[0].config
        ):
            raise CoordinatorError("activity.reasoning_unconfigured")
        try:
            config = ReasoningToolConfig.model_validate(attached[0].config)
        except ValidationError as exc:
            raise CoordinatorError("activity.reasoning_unconfigured") from exc
        if ANSWER_TASK not in config.tasks:
            raise CoordinatorError("activity.reasoning_unconfigured")
        return ReasoningTool(config)

    async def _live(self, lease: TurnLease) -> None:
        """The turn still owns its conversation: no write after the lease is lost."""
        if not await self._turns(lease):
            raise TurnOwnershipLost("The turn no longer owns this conversation")

    # --- Activity Context ---------------------------------------------------------------------

    async def _owned(
        self, actor: ActivityActor, event: ClientEventV2, context_id: str
    ) -> ActivityContext:
        """This conversation's own, active context of this activity (others are not found)."""
        context = await self._contexts.get(actor, context_id)
        owner = OwnerRef(kind=CONVERSATION_OWNER, id=event.conversation_id)
        if context.owner != owner or context.activity.key != self.activity:
            raise ActivityNotFound("Activity context not found")
        if context.status != "active":
            raise ActivityClosed("The activity context is closed")
        return context

    async def _resume(
        self, actor: ActivityActor, owner: OwnerRef, draft_id: str
    ) -> ActivityContext | None:
        """The active generation, settled first. Only the same activity for the same draft
        resumes; any other active activity is refused."""
        current = await self._contexts.current(actor, owner)
        if current is not None and current.reconciliation_pending:
            await self._contexts.reconcile(actor, owner)
            current = await self._contexts.current(actor, owner)
        if current is None:
            return None
        context = current.context
        draft = context.references.get("draft")
        if context.activity.key != self.activity or draft is None or draft.id != draft_id:
            raise ActivityActiveExists("An activity is already active for this owner")
        return context

    async def _next_generation(self, actor: ActivityActor, owner: OwnerRef) -> int:
        history = await self._contexts.history(actor, owner)
        return max((c.generation for c in history), default=0) + 1

    # --- Manuscript ---------------------------------------------------------------------------

    async def _invoke(
        self, principal: AuthorizedAgentPrincipal, op: Any, command: dict[str, Any]
    ) -> dict[str, Any]:
        result = await invoke_manuscript(self._authority, self._exchange, principal, op, command)
        if not isinstance(result, dict) or not result.get("ok"):
            code = result.get("code") if isinstance(result, dict) else None
            raise CoordinatorError(
                code if isinstance(code, str) else "manuscript.unavailable",
                retryable=bool(isinstance(result, dict) and result.get("retryable")),
            )
        return result

    async def _view(
        self,
        principal: AuthorizedAgentPrincipal,
        context: ActivityContext,
        manuscript: ManuscriptDefinition,
    ) -> dict[str, Any]:
        """The Manuscript view of the context's session: the session ID comes only from
        the Activity Context, and the session must run the declared Manuscript."""
        session_id = context.references["manuscript_session"].id
        view = (
            await self._invoke(
                principal,
                "status",
                {
                    "contract": "caos.manuscript_session.status",
                    "schema_version": 1,
                    "session_id": session_id,
                },
            )
        )["session"]
        return self._pinned(view, manuscript)

    @staticmethod
    def _pinned(view: dict[str, Any], manuscript: ManuscriptDefinition) -> dict[str, Any]:
        pinned = view["manuscript"]
        if (pinned["id"], pinned["version"], pinned["fingerprint"]) != (
            manuscript.id,
            manuscript.version,
            manuscript.fingerprint(),
        ):
            # The session runs a different Manuscript than the Agent now declares.
            raise CoordinatorError("activity.manuscript_changed")
        return view

    async def _catalog(
        self, principal: AuthorizedAgentPrincipal, specs: list[FieldSpec]
    ) -> tuple[dict[str, list[dict[str, str]]], set[str]]:
        """One bounded page of the human's own catalog per catalog field (keys and short
        titles only); a field whose page is not the whole catalog is ``incomplete``."""
        options: dict[str, list[dict[str, str]]] = {}
        incomplete: set[str] = set()
        for spec in specs:
            projected = await project_catalog(
                self._authority,
                self._exchange,
                principal,
                {
                    "contract": "caos.catalog.project",
                    "schema_version": 1,
                    "operation": "list",
                    "request": {
                        "kind": CATALOG_KINDS[spec.catalog or ""],
                        "limit": MAX_CATALOG_OPTIONS,
                    },
                },
            )
            if not isinstance(projected, dict) or not projected.get("ok"):
                raise CoordinatorError("activity.catalog_unavailable", retryable=True)
            page = projected["result"]
            options[spec.key] = [
                {"key": entry["ref"]["key"], "title": entry["identity"]["title"][:MAX_LABEL]}
                for entry in page["entries"]
                if len(entry["ref"]["key"]) <= 128
            ]
            if page.get("next_after") is not None:
                incomplete.add(spec.key)
        return options, incomplete

    def _result(
        self,
        context: ActivityContext,
        view: dict[str, Any],
        manuscript: ManuscriptDefinition,
        interpretation: dict[str, Any],
    ) -> dict[str, Any]:
        return self._envelope(
            context,
            resumed=False,
            manuscript=_summary(view, manuscript),
            prompt=prompt(view, manuscript),
            interpretation=interpretation,
        )

    async def _outcome(
        self,
        principal: AuthorizedAgentPrincipal,
        context: ActivityContext,
        manuscript: ManuscriptDefinition,
        *,
        resumed: bool,
    ) -> dict[str, Any]:
        view = await self._view(principal, context, manuscript)
        return self._envelope(
            context,
            resumed=resumed,
            manuscript=_summary(view, manuscript),
            prompt=prompt(view, manuscript),
        )

    def _envelope(
        self,
        context: ActivityContext,
        *,
        resumed: bool,
        manuscript: dict[str, Any] | None,
        prompt: dict[str, Any] | None,
        interpretation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        draft = context.references.get("draft")
        if context.status != "active" or manuscript is None:
            action = "none"
        elif manuscript["status"] == "active" and manuscript["ready_to_complete"]:
            action = "complete"
        elif manuscript["status"] == "active" and manuscript["confirmations_pending"]:
            # Only explicit boolean confirmations remain: never answered by Reasoning.
            action = "confirm"
        elif manuscript["status"] == "active":
            action = "answer"
        else:
            action = "none"
        return {
            "contract": OUTCOME_CONTRACT,
            # 2 (COORD-0.2): ``interpretation``, ``confirmations_pending`` and ``confirm``.
            "schema_version": 2,
            "activity": self.activity,
            "agent_id": context.actor.principal_id,
            "context": {
                "context_id": context.context_id,
                "status": context.status,
                "closed_reason": context.closed_reason,
                "generation": context.generation,
                "revision": context.revision,
                "draft_id": None if draft is None else draft.id,
            },
            "resumed": resumed,
            "manuscript": manuscript,
            "prompt": prompt,
            "next_action": action,
            "interpretation": interpretation,
            "authorizes": dict(_NOT_AUTHORIZED),
        }


def _summary(view: Mapping[str, Any], manuscript: ManuscriptDefinition) -> dict[str, Any]:
    stage = manuscript.stage(view["stage"])
    pending = [key for key in view["missing"] if manuscript.field(key).type == "boolean"]
    return {
        "session_id": view["session_id"],
        "manuscript": manuscript.key(),
        "revision": view["revision"],
        "status": view["status"],
        "stage": stage.key,
        "completed_stages": view["completed_stages"],
        "missing": view["missing"],
        # Only boolean confirmations are missing from the current stage.
        "confirmations_pending": bool(pending) and len(pending) == len(view["missing"]),
        "ready_to_complete": view["ready_to_complete"],
    }


# --- Interpretation (deterministic; the model's output is a candidate, never a decision) ---


def _interpretation(status: str, **parts: Any) -> dict[str, Any]:
    """Codes and field keys only: never model text, evidence or the human's message."""
    return {
        "status": status,
        "applied": [],
        "unresolved": [],
        "rejected": {},
        "issues": [],
        "failure": None,
        "catalog_incomplete": [],
        "confirmation_required": [],
        "unsupported": [],
        **parts,
    }


def _supported(spec: FieldSpec) -> bool:
    """Whether the published task can express this field without widening its limits."""
    if spec.type == "enum":
        options = spec.options or []
        return len(options) <= MAX_OPTIONS and all(len(o) <= MAX_OPTION_CHARS for o in options)
    if spec.type == "text":
        return spec.min_length is None or spec.min_length <= MAX_TEXT_VALUE
    return spec.type in ("text_list", "catalog_ref", "catalog_ref_list")


def _value_limit(spec: FieldSpec) -> int:
    """The task's ceiling, never above the Manuscript's own: longer text (e.g. full Agent
    instructions) is not extractable and is rejected, never silently truncated."""
    return min(spec.max_length or MAX_TEXT_VALUE, MAX_TEXT_VALUE)


def _guidance(manuscript: ManuscriptDefinition, stage: Stage) -> str:
    """The stage's own guidance section (``## <anchor>``), bounded. Expression data only."""
    if not stage.guidance:
        return ""
    lines = manuscript.guidance.splitlines()
    try:
        start = lines.index(f"## {stage.guidance}") + 1
    except ValueError:
        return ""
    section: list[str] = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        section.append(line)
    return "\n".join(section).strip()[:MAX_GUIDANCE]


def _task_field(
    spec: FieldSpec, stage: Stage, options: Mapping[str, list[dict[str, str]]]
) -> dict[str, Any]:
    field: dict[str, Any] = {
        "key": spec.key,
        "label": spec.title[:MAX_LABEL],
        "type": spec.type,
        "required": spec.key in stage.required,
    }
    if spec.type == "enum":
        field["options"] = list(spec.options or [])
    if spec.type == "text":
        field["max_length"] = _value_limit(spec)
        if spec.min_length is not None:
            field["min_length"] = spec.min_length
    if spec.type in ("text_list", "catalog_ref_list"):
        field["max_items"] = min(spec.max_items or MAX_LIST_ITEMS, MAX_LIST_ITEMS)
    if spec.catalog is not None:
        field["catalog_options"] = list(options.get(spec.key, []))
    return field


def _task_input(
    message: str,
    manuscript: ManuscriptDefinition,
    stage: Stage,
    offered: list[FieldSpec],
    view: Mapping[str, Any],
    options: dict[str, list[dict[str, str]]],
) -> tuple[dict[str, Any], set[str]] | None:
    """The bounded task input for the current stage, or ``None`` if even its smallest
    form exceeds the task's byte budget. Catalog pages are halved (largest first,
    deterministically) until it fits; a trimmed field is ``incomplete``."""
    keys = {spec.key for spec in offered}
    answers = view.get("answers") or {}
    trimmed: set[str] = set()
    options = {key: list(values) for key, values in options.items()}
    budget = MANUSCRIPT_ANSWER_EXTRACTION.budgets.max_input_bytes

    def build() -> dict[str, Any]:
        return {
            "message": message,
            "stage": {"key": stage.key, "title": stage.title[:MAX_LABEL]},
            "fields": [_task_field(spec, stage, options) for spec in offered][:MAX_TASK_FIELDS],
            "current_answers": {k: v for k, v in answers.items() if k in keys},
            "guidance_excerpt": _guidance(manuscript, stage),
        }

    task_input = build()
    while len(canonical_json(task_input).encode()) > budget:
        candidates = sorted(
            (key for key, values in options.items() if values),
            key=lambda key: (-len(options[key]), key),
        )
        if not candidates:
            return None
        largest = candidates[0]
        options[largest] = options[largest][: len(options[largest]) // 2]
        trimmed.add(largest)
        task_input = build()
    return task_input, trimmed


def _within(spec: FieldSpec, value: Any, options: Mapping[str, list[dict[str, str]]]) -> bool:
    """Coordinator-side bounds, before Manuscript's authoritative typed validation."""
    if spec.type == "text":
        return isinstance(value, str) and 0 < len(value) <= _value_limit(spec)
    if spec.type == "enum":
        return isinstance(value, str) and value in (spec.options or [])
    keys = {option["key"] for option in options.get(spec.key, [])}
    if spec.type == "catalog_ref":
        return isinstance(value, str) and value in keys
    if not isinstance(value, list) or not value or len(value) != len(set(value)):
        return False
    if len(value) > min(spec.max_items or MAX_LIST_ITEMS, MAX_LIST_ITEMS):
        return False
    if spec.type == "catalog_ref_list":
        return all(isinstance(item, str) and item in keys for item in value)
    return all(isinstance(item, str) and 0 < len(item) <= MAX_TEXT_VALUE for item in value)


def _filter(
    result: Mapping[str, Any],
    offered: list[FieldSpec],
    options: Mapping[str, list[dict[str, str]]],
    incomplete: set[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Accepted values for the Manuscript and the interpretation summary.

    Only verifier-accepted, current-stage, high- or medium-confidence candidates within
    bounds survive; everything else is unresolved or rejected with a code."""
    specs = {spec.key: spec for spec in offered}
    status = result.get("status") if isinstance(result, Mapping) else None
    if status not in ("interpreted", "unresolved", "empty"):
        failure = result.get("failure") if isinstance(result, Mapping) else None
        failure = failure if isinstance(failure, Mapping) else {}
        code = failure.get("code")
        return {}, _interpretation(
            "failed",
            failure={
                "code": code if isinstance(code, str) else "reasoning.unavailable",
                "retryable": failure.get("retryable") is True,
            },
        )
    output = result.get("output") or {}
    issues = {
        issue["code"]
        for issue in result.get("issues") or ()
        if isinstance(issue, Mapping) and isinstance(issue.get("code"), str)
    }
    accepted: dict[str, Any] = {}
    unresolved: dict[str, dict[str, Any]] = {}
    rejected: dict[str, str] = {}
    seen: set[str] = set()
    for candidate in output.get("accepted") or ():
        key = candidate.get("field")
        if key not in specs:
            issues.add("candidate.field_out_of_stage")  # Never echo an unknown name.
            continue
        if key in seen:
            accepted.pop(key, None)
            unresolved[key] = {"field": key, "reason": "conflicting"}
            continue
        seen.add(key)
        value = candidate.get("value")
        if value is None:
            rejected[key] = "candidate.value_missing"
        elif candidate.get("confidence") not in ("high", "medium"):
            unresolved[key] = {"field": key, "reason": "low_confidence"}
        elif not _within(specs[key], value, options):
            rejected[key] = "candidate.out_of_bounds"
        else:
            accepted[key] = value
    for item in output.get("unresolved") or ():
        key = item.get("field")
        if key in specs and key not in accepted:
            entry: dict[str, Any] = {"field": key, "reason": str(item.get("reason"))}
            if specs[key].type in ("enum", "catalog_ref", "catalog_ref_list"):
                # Option values the human was offered, never free model text.
                allowed = set(specs[key].options or []) | {o["key"] for o in options.get(key, [])}
                entry["alternatives"] = [a for a in item.get("alternatives") or () if a in allowed]
            unresolved.setdefault(key, entry)
    for key in output.get("clarification_fields") or ():
        if key in specs and key not in accepted:
            unresolved.setdefault(key, {"field": key, "reason": "clarification_requested"})
    for key in sorted(incomplete):
        if key not in accepted and key not in unresolved:
            unresolved[key] = {"field": key, "reason": "catalog_incomplete"}
    return accepted, _interpretation(
        "clarification",
        unresolved=[unresolved[key] for key in sorted(unresolved)],
        rejected=dict(sorted(rejected.items())),
        issues=sorted(issues),
        catalog_incomplete=sorted(incomplete),
    )


def prompt(view: Mapping[str, Any], manuscript: ManuscriptDefinition) -> dict[str, Any] | None:
    """The current stage's unanswered fields, deterministically, from the Manuscript view
    and its pinned definition only (no model, no guidance parsing)."""
    if view.get("status") != "active":
        return None
    stage = manuscript.stage(view["stage"])
    answers = view.get("answers") or {}
    fields: list[dict[str, Any]] = []
    for key in stage.collects:
        if answers.get(key) not in (None, "", []):
            continue
        spec = manuscript.field(key)
        fields.append(
            {
                "key": spec.key,
                "title": spec.title,
                "type": spec.type,
                "required": key in stage.required,
                **({"options": list(spec.options)} if spec.options else {}),
                **({"catalog": spec.catalog} if spec.catalog else {}),
                # An explicit human decision; never inferred from a message.
                **({"confirmation": True} if spec.type == "boolean" else {}),
            }
        )
    labels = [f["title"] + ("" if f["required"] else " (optional)") for f in fields]
    return {
        "stage": stage.key,
        "title": stage.title,
        "guidance": stage.guidance,
        "fields": fields,
        "text": f"{stage.title}: please provide {', '.join(labels)}."
        if labels
        else f"{stage.title}: ready to continue.",
    }
