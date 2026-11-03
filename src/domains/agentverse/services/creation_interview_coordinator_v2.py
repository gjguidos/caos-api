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

Idempotency: keys are derived, never chosen by the client. A start is keyed by
``(conversation, activity, draft, generation)`` so that a retry under a *new* event ID
(the journal settles an interrupted turn as ``interrupted``) converges on the same
Manuscript session and context instead of creating second ones. A close is keyed by the
turn's event ID; a closed context with the requested reason is reported, not reclosed.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from pydantic import ValidationError

from src.base.events.v2.contracts import EventPermissionError
from src.base.operations.v2.admission import sha256_hex
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
from src.domains.agentverse.entities.conversation_v2 import (
    ActivityCloseV2,
    ActivityStartV2,
    ClientEventV2,
)
from src.domains.agentverse.manuscripts.capability import SIGNALS as MANUSCRIPT_SIGNALS
from src.domains.agentverse.manuscripts.capability import invoke_manuscript
from src.domains.agentverse.manuscripts.models import ManuscriptDefinition
from src.domains.agentverse.manuscripts.service import ManuscriptService
from src.domains.agentverse.manuscripts.sessions import ManuscriptError
from src.domains.agentverse.ncsc.cpbx import SynapticExchange
from src.domains.agentverse.registries import tool_registry_instance
from src.domains.agentverse.registries.tool_registry import ToolRegistry
from src.domains.agentverse.repositories.conversation_v2 import TurnLease
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey
from src.domains.agentverse.security.utu import AuthorizedAgentPrincipal, UTUAuthority
from src.domains.agentverse.tools.manuscript_tool.spec import ManuscriptToolConfig, manuscript_tool
from src.domains.agentverse.tools.spec import AgentToolRef

INTERVIEW: Final = "agent.creation_interview@1.0.0"
OUTCOME_CONTRACT: Final = "caos.activity_turn"
DRAFT_ARTIFACT: Final = "caos.agent_creation_draft"
MANUSCRIPT_TOOL: Final = "manuscript"
# Exactly what each step reaches; checked before any mutation, re-checked by providers.
START_GRANTS: Final = frozenset(
    {TURN_SIGNAL, MANUSCRIPT_SIGNALS["start"], MANUSCRIPT_SIGNALS["status"]}
)
CLOSE_GRANTS: Final = frozenset({TURN_SIGNAL})
_NOT_AUTHORIZED: Final = {
    "draft_write": False,
    "approval": False,
    "creation": False,
    "activation": False,
}


class CoordinatorError(Exception):
    """Typed, requester-safe failure (codes only; never another owner's resources)."""

    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


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
        tools: ToolRegistry = tool_registry_instance,
    ) -> None:
        self._authority = authority
        self._exchange = exchange
        self._contexts = contexts
        self._manuscripts = manuscripts
        self._gate = gate
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

    # --- Activity Context ---------------------------------------------------------------------

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

    async def _outcome(
        self,
        principal: AuthorizedAgentPrincipal,
        context: ActivityContext,
        manuscript: ManuscriptDefinition,
        *,
        resumed: bool,
    ) -> dict[str, Any]:
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
        pinned = view["manuscript"]
        if (pinned["id"], pinned["version"], pinned["fingerprint"]) != (
            manuscript.id,
            manuscript.version,
            manuscript.fingerprint(),
        ):
            # The session runs a different Manuscript than the Agent now declares.
            raise CoordinatorError("activity.manuscript_changed")
        summary = {
            "session_id": view["session_id"],
            "manuscript": manuscript.key(),
            "revision": view["revision"],
            "status": view["status"],
            "stage": view["stage"],
            "completed_stages": view["completed_stages"],
            "missing": view["missing"],
            "ready_to_complete": view["ready_to_complete"],
        }
        return self._envelope(
            context, resumed=resumed, manuscript=summary, prompt=prompt(view, manuscript)
        )

    def _envelope(
        self,
        context: ActivityContext,
        *,
        resumed: bool,
        manuscript: dict[str, Any] | None,
        prompt: dict[str, Any] | None,
    ) -> dict[str, Any]:
        draft = context.references.get("draft")
        if context.status != "active" or manuscript is None:
            action = "none"
        elif manuscript["status"] == "active" and manuscript["ready_to_complete"]:
            action = "complete"
        elif manuscript["status"] == "active":
            action = "answer"
        else:
            action = "none"
        return {
            "contract": OUTCOME_CONTRACT,
            "schema_version": 1,
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
            "authorizes": dict(_NOT_AUTHORIZED),
        }


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
