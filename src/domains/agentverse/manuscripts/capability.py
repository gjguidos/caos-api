"""``manuscript.session.*@1.0.0``: Enki's governed access to the Manuscript runtime.

Six exact-version signals, provider ``system.manuscript``, each executable only under its
own single-signal attenuated scope (so an unscoped client, the generic signal route, or
another Manuscript signal's scope cannot reach it). Tenant, human and source Agent come
only from the CPBX-issued, session-origin permit, re-verified against current UTU
authority on every call. A session ID is a reference, never authority: ownership and
Manuscript visibility are rechecked by the provider and the runtime. The runtime holds
no draft-write, proposal, approval, creation, Store, Genesis or Khepri handle, and a
definition's declared handoff is metadata that grants and invokes nothing.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints, ValidationError

from src.base.events.v2.contracts import EventPermissionError
from src.domains.agentverse.manuscripts.models import ManuscriptDefinition
from src.domains.agentverse.manuscripts.service import ManuscriptActor, ManuscriptService
from src.domains.agentverse.manuscripts.sessions import ManuscriptError
from src.domains.agentverse.ncsc.cpbx import AuthorizedSynapticClient, SynapticExchange
from src.domains.agentverse.ncsc.models import Synapse
from src.domains.agentverse.registries.synaptic_registry import SynapticBinding, SynapticRegistry
from src.domains.agentverse.security.capability_scope import CapabilityBinding, CapabilityScope
from src.domains.agentverse.security.revocation import ExecutionPermit
from src.domains.agentverse.security.utu import AuthorizedAgentPrincipal, UTUAuthority
from src.domains.agentverse.tools.spec import SynapticCapabilitySpec, SynapticToolSpec

PROVIDER = "system.manuscript"
VERSION = "1.0.0"
Operation = Literal["start", "status", "answer", "correct", "restart", "complete"]
OPERATIONS: tuple[Operation, ...] = ("start", "status", "answer", "correct", "restart", "complete")
SIGNALS: dict[str, str] = {op: f"manuscript.session.{op}" for op in OPERATIONS}
BINDINGS: dict[str, CapabilityBinding] = {
    op: CapabilityBinding(signal, PROVIDER, VERSION) for op, signal in SIGNALS.items()
}
SCOPES: dict[str, CapabilityScope] = {
    op: CapabilityScope(SIGNALS[op], binding, frozenset({binding}))
    for op, binding in BINDINGS.items()
}
RESULT_CONTRACT = "caos.manuscript_session_result"

RequestKey = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{16,128}$")]
SessionId = Annotated[str, StringConstraints(min_length=1, max_length=64)]
Answers = Annotated[dict[str, Any], Field(max_length=32)]


class _Command(BaseModel):
    """Identity-free commands: no tenant, owner, Agent, permit or grant is expressible."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    schema_version: StrictInt = Field(ge=1, le=1)


class StartCommand(_Command):
    contract: Literal["caos.manuscript_session.start"]
    manuscript: Annotated[str, StringConstraints(max_length=100)]
    idempotency_key: RequestKey
    draft_id: Annotated[str, StringConstraints(max_length=64)] | None = None
    prefill: Answers = Field(default_factory=dict)


class _SessionCommand(_Command):
    """A command about an existing session. The ID is a reference, never authority."""

    session_id: SessionId


class StatusCommand(_SessionCommand):
    contract: Literal["caos.manuscript_session.status"]


class AnswerCommand(_SessionCommand):
    contract: Literal["caos.manuscript_session.answer"]
    expected_revision: StrictInt = Field(ge=1)
    answers: Answers
    request_key: RequestKey


class CorrectCommand(_SessionCommand):
    contract: Literal["caos.manuscript_session.correct"]
    expected_revision: StrictInt = Field(ge=1)
    stage: Annotated[str, StringConstraints(max_length=64)]
    answers: Answers = Field(default_factory=dict)
    request_key: RequestKey


class RestartCommand(_SessionCommand):
    contract: Literal["caos.manuscript_session.restart"]
    request_key: RequestKey


class CompleteCommand(_SessionCommand):
    contract: Literal["caos.manuscript_session.complete"]
    expected_revision: StrictInt = Field(ge=1)
    request_key: RequestKey


COMMANDS: dict[str, type[_Command]] = {
    "start": StartCommand,
    "status": StatusCommand,
    "answer": AnswerCommand,
    "correct": CorrectCommand,
    "restart": RestartCommand,
    "complete": CompleteCommand,
}

# Who may run a Manuscript: one whose artifact is an Agent creation draft is visible only
# to a source Agent with a reviewed validation-gateway binding for that tenant and human
# (the same binding that lets the human review drafts for it).
Visibility = Callable[[ManuscriptActor, ManuscriptDefinition], bool]


def gateway_visibility(bindings: Iterable[Any]) -> Visibility:
    allowed = frozenset((b.tenant_id, b.subject_id, b.source_agent_id) for b in bindings)

    def visible(actor: ManuscriptActor, definition: ManuscriptDefinition) -> bool:
        if definition.artifact.contract != "caos.agent_creation_draft":
            return False
        return (actor.tenant_id, actor.subject_id, actor.agent_id) in allowed

    return visible


class ManuscriptCapability:
    def __init__(
        self,
        *,
        authority: UTUAuthority,
        manuscripts: ManuscriptService,
        visible: Visibility,
    ) -> None:
        self._authority = authority
        self._manuscripts = manuscripts
        self._visible = visible

    def handler(
        self, op: Operation
    ) -> Callable[[Synapse, AuthorizedSynapticClient], Awaitable[Any]]:
        async def handle(synapse: Synapse, client: AuthorizedSynapticClient) -> dict[str, Any]:
            return await self._handle(op, synapse, client)

        handle.__qualname__ = f"ManuscriptCapability.{op}"
        handle.__module__ = __name__
        return handle

    async def _authorize(
        self, op: Operation, synapse: Synapse, client: AuthorizedSynapticClient
    ) -> tuple[ExecutionPermit, ManuscriptActor]:
        """Independent provider-side verification of the exact scoped caller."""
        permit = client.execution_permit if isinstance(client, AuthorizedSynapticClient) else None
        if (
            not isinstance(permit, ExecutionPermit)
            or (synapse.signal, synapse.target, synapse.tenant_id, synapse.source)
            != (SIGNALS[op], PROVIDER, permit.key.tenant_id, permit.key.agent_id)
            or permit.scope != SCOPES[op]
            or not permit.binding_chain
            or permit.binding_chain[-1] != BINDINGS[op]
            or synapse.entry_operation != SCOPES[op].operation
            or synapse.provider_version != VERSION
            or permit.origin != "session"
        ):
            raise EventPermissionError(f"{SIGNALS[op]} requires its own verified scoped caller")
        await self._authority.check_permit(permit)
        current = await self._authority.resolve_agent_principal(permit.key)
        if (
            current.permit.versions != permit.versions
            or SIGNALS[op] not in self._authority.grants_for(current).invoke
        ):
            raise EventPermissionError("Manuscript authority changed")
        # The human is resolved from the trusted session adapter, never the payload.
        return permit, await ManuscriptActor.from_permit(self._authority, permit)

    async def _handle(
        self, op: Operation, synapse: Synapse, client: AuthorizedSynapticClient
    ) -> dict[str, Any]:
        permit, actor = await self._authorize(op, synapse, client)
        try:
            command = COMMANDS[op].model_validate(synapse.payload)
        except ValidationError:
            return self._failure("manuscript.invalid_request")
        manuscripts = self._manuscripts
        try:
            if isinstance(command, StartCommand):
                definition = manuscripts.definition(command.manuscript)
                if not self._visible(actor, definition):
                    return self._failure("manuscript.unavailable")
                session, rejected = await manuscripts.start(
                    actor,
                    command.manuscript,
                    idempotency_key=command.idempotency_key,
                    draft_id=command.draft_id,
                    prefill=command.prefill,
                )
                result = self._result(session, definition, rejected=rejected)
            else:
                assert isinstance(command, _SessionCommand)
                # The referenced session is loaded only for this verified actor (others
                # are indistinguishable from missing); visibility is rechecked each time.
                session, definition = await manuscripts.owned(actor, command.session_id)
                if not self._visible(actor, definition):
                    return self._failure("session.not_found")
                if isinstance(command, StatusCommand):
                    result = self._result(session, definition)
                else:
                    result = await self._apply(actor, command, definition)
        except ManuscriptError as exc:
            return self._failure(exc.code, exc)
        # Never release a result to a caller whose authority ended meanwhile.
        await self._authority.check_permit(permit)
        return result

    async def _apply(
        self, actor: ManuscriptActor, command: _Command, definition: ManuscriptDefinition
    ) -> dict[str, Any]:
        manuscripts = self._manuscripts
        if isinstance(command, AnswerCommand):
            session, outcome = await manuscripts.answer(
                actor,
                command.session_id,
                expected_revision=command.expected_revision,
                answers=command.answers,
                request_key=command.request_key,
            )
            return self._result(session, definition, outcome=outcome)
        if isinstance(command, CorrectCommand):
            session, outcome = await manuscripts.correct(
                actor,
                command.session_id,
                expected_revision=command.expected_revision,
                stage=command.stage,
                answers=command.answers,
                request_key=command.request_key,
            )
            return self._result(session, definition, outcome=outcome)
        if isinstance(command, RestartCommand):
            session = await manuscripts.restart(
                actor, command.session_id, request_key=command.request_key
            )
            return self._result(session, definition)
        assert isinstance(command, CompleteCommand)
        session = await manuscripts.complete(
            actor,
            command.session_id,
            expected_revision=command.expected_revision,
            request_key=command.request_key,
        )
        return self._result(session, definition)

    def _result(
        self,
        session: Any,
        definition: ManuscriptDefinition,
        *,
        outcome: Any = None,
        rejected: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return {
            "contract": RESULT_CONTRACT,
            "ok": True,
            "session": self._manuscripts.view(session, definition),
            "outcome": None
            if outcome is None
            else {
                "status": outcome.status,
                "accepted": list(outcome.accepted),
                "rejected": dict(outcome.rejected),
                "missing": list(outcome.missing),
            },
            "rejected_prefill": rejected or {},
        }

    @staticmethod
    def _failure(code: str, exc: Exception | None = None) -> dict[str, Any]:
        return {
            "contract": RESULT_CONTRACT,
            "ok": False,
            "code": code,
            "retryable": bool(getattr(exc, "retryable", False)),
            **(
                {"current_revision": exc.current_revision}
                if exc is not None and hasattr(exc, "current_revision")
                else {}
            ),
            **(
                {"missing": list(exc.missing)}
                if exc is not None and hasattr(exc, "missing")
                else {}
            ),
        }


def register_manuscript_capability(
    registry: SynapticRegistry, capability: ManuscriptCapability
) -> list[SynapticBinding]:
    """Trusted bootstrap registration of the six exact signals. Grants nothing."""
    return [
        registry.register(
            SIGNALS[op],
            description=f"Deterministic Manuscript session {op} for the caller's own human",
            version=VERSION,
            metadata={
                "access": "system",
                "deterministic": True,
                "synaptic": SynapticToolSpec(
                    capabilities=[SynapticCapabilitySpec(signal=SIGNALS[op], access="system")]
                ).model_dump(),
            },
            component=SynapticBinding(SIGNALS[op], PROVIDER, capability.handler(op), VERSION),
        )
        for op in OPERATIONS
    ]


async def invoke_manuscript(
    authority: UTUAuthority,
    exchange: SynapticExchange,
    principal: AuthorizedAgentPrincipal,
    op: Operation,
    command: dict[str, Any],
) -> dict[str, Any]:
    """Trusted in-process caller for an Agent runtime (Enki's future turn plan): narrows
    the verified principal to exactly one Manuscript signal before binding a client."""
    scoped = await authority.attenuate(principal, SCOPES[op])
    client = exchange.bind(scoped)  # NEVER bind the unattenuated principal.
    try:
        result = await client.request(SIGNALS[op], command, target=PROVIDER, version=VERSION)
    finally:
        client.revoke()
        await client.drain()
    return result["result"] if isinstance(result, dict) and "result" in result else result
