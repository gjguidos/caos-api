"""Activation and readiness of a Genesis-provisioned agent: separate authority, real evidence.

Creation never implies activation. Only a handoff whose reviewed policy opts in
(``activation="requester_session"``) reaches this step, and even then Khepri resolves
the new agent's own UTU authority: its approval, the requester session's membership,
its persisted definition and its grants. A denial is recorded, not retried around.

Readiness is observed, never assumed: the agent's own CPBX client invokes the
deterministic ``chat.context`` capability under its permit, its UTU grants must
contain both conversational capabilities, and its persisted model backend must be a
real, runtime-capable catalog entry. No model inference is performed.

Evidence: one activation and one readiness record per authority (permit evidence
digest) and outcome, correlated with the Genesis operation and caused by its store
record. Retrying the same request in the same session re-records identical facts
idempotently.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, Literal, Protocol

from src.base.events.v2.contracts import EventPermissionError
from src.base.infrastructure.ai.client_factory import ClientFactory
from src.base.operations.v2.admission import AuthorizationProvenance
from src.domains.agentverse.audit.models import LogEvent, stable_event_id
from src.domains.agentverse.audit.service import AuditSink
from src.domains.agentverse.operations.admission import permit_provenance
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceKey
from src.domains.agentverse.security.revocation import ExecutionPermit
from src.domains.agentverse.security.utu import UTUAuthority

if TYPE_CHECKING:
    from src.domains.agentverse.agents.genesis.initiation import GenesisHandoff

logger = logging.getLogger(__name__)

ACTIVATION_EVENT = "genesis.activation.completed"
READINESS_EVENT = "genesis.readiness.completed"
# Audit targets: Khepri's lifecycle operation and the probed capability.
ACTIVATION_TARGET = ("agent.lifecycle.activate", "1.0.0")
READINESS_PROBE = ("chat.context", "1.0.0")
CONVERSATION_GRANTS = frozenset({"chat.context", "chat.response"})


class Lifecycle(Protocol):
    async def awaken(self, key: AgentInstanceKey) -> Any: ...


class Instances(Protocol):
    def get(self, key: AgentInstanceKey) -> Any: ...


class Definitions(Protocol):
    async def find_by_id(self, agent_id: str) -> Mapping[str, Any] | None: ...


class GenesisActivation:
    def __init__(
        self,
        *,
        khepri: Lifecycle,
        instances: Instances,
        authority: UTUAuthority,
        definitions: Definitions,
        catalog: Callable[[], ClientFactory | None],
        log: AuditSink,
    ) -> None:
        self._khepri = khepri
        self._instances = instances
        self._authority = authority
        self._definitions = definitions
        self._catalog = catalog
        self._log = log

    async def activate(
        self,
        *,
        requester: ExecutionPermit,
        handoff: GenesisHandoff,
        correlation_id: str,
        causation_id: str,
    ) -> dict[str, Any]:
        """Awaken the provisioned agent in the requester's live session, then probe it."""
        # Same tenant, user and session as the verified request; a different agent.
        key = AgentInstanceKey(
            requester.key.tenant_id,
            requester.key.user_id,
            handoff.agent_id,
            requester.key.session_id,
        )
        try:
            result = await self._khepri.awaken(key)
            principal = await self._authority.resolve_agent_principal(key)
        except EventPermissionError:
            # The requester's authority is the evidence of a refused follow-on.
            await self._record_activation(
                handoff,
                permit_provenance(requester, ACTIVATION_TARGET[0]),
                "rejected",
                state="denied",
                code="activation.denied",
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
            return {"state": "denied", "ready": False}
        except Exception as exc:  # noqa: BLE001 - recorded as a failed activation
            logger.warning("Genesis activation of %s failed: %s", handoff.agent_id, exc)
            await self._record_activation(
                handoff,
                permit_provenance(requester, ACTIVATION_TARGET[0]),
                "failed",
                state="failed",
                code="activation.failed",
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
            return {"state": "failed", "ready": False}
        permit = principal.permit
        activated = await self._record_activation(
            handoff,
            permit_provenance(permit, ACTIVATION_TARGET[0]),
            "succeeded",
            state="active",
            already_active=bool(getattr(result, "already_active", False)),
            correlation_id=correlation_id,
            causation_id=causation_id,
        )
        ready, readiness_id = await self._readiness(
            key, handoff, principal, permit, correlation_id=correlation_id, causation_id=activated
        )
        return {
            "state": "active",
            "ready": ready,
            "activation_event_id": activated,
            "readiness_event_id": readiness_id,
        }

    async def _readiness(
        self,
        key: AgentInstanceKey,
        handoff: GenesisHandoff,
        principal: Any,
        permit: ExecutionPermit,
        *,
        correlation_id: str,
        causation_id: str,
    ) -> tuple[bool, str]:
        grants_ok = CONVERSATION_GRANTS <= self._authority.grants_for(principal).invoke
        backend_ok = await self._backend_ready(handoff.agent_id)
        context_ok, code = False, None
        runtime = self._instances.get(key)
        client = getattr(runtime, "ncsc", None)
        if client is None:
            code = "readiness.runtime_missing"
        else:
            try:
                context = await client.request(
                    READINESS_PROBE[0],
                    {"text": "readiness", "history": []},
                    correlation_id=correlation_id,
                    causation_id=causation_id,
                )
                context_ok = isinstance(context, dict) and isinstance(context.get("messages"), list)
                code = None if context_ok else "readiness.context_invalid"
            except EventPermissionError:
                code = "readiness.context_denied"
            except Exception as exc:  # noqa: BLE001 - an observed probe failure
                logger.warning("Genesis readiness probe of %s failed: %s", handoff.agent_id, exc)
                code = "readiness.context_failed"
        if code is None and not grants_ok:
            code = "readiness.grants_missing"
        if code is None and not backend_ok:
            code = "readiness.backend_unavailable"
        ready = context_ok and grants_ok and backend_ok
        outcome = "succeeded" if ready else "failed"
        provenance = permit_provenance(permit, READINESS_PROBE[0])
        event_id = await self._append(
            READINESS_EVENT,
            outcome,
            handoff,
            provenance,
            READINESS_PROBE,
            {
                "slot": handoff.slot,
                "probe": READINESS_PROBE[0],
                "context_ok": context_ok,
                "grants_ok": grants_ok,
                "backend_ok": backend_ok,
                "code": code,
            },
            correlation_id=correlation_id,
            causation_id=causation_id,
        )
        return ready, event_id

    async def _backend_ready(self, agent_id: str) -> bool:
        definition = await self._definitions.find_by_id(agent_id)
        factory = self._catalog()
        if not definition or not isinstance(factory, ClientFactory):
            return False
        repo = factory.get_repo("llm", definition.get("llm_type") or "")
        return (
            repo is not None
            and not getattr(repo, "is_noop", False)
            and callable(getattr(repo, "get_response_async", None))
        )

    async def _record_activation(
        self,
        handoff: GenesisHandoff,
        provenance: AuthorizationProvenance,
        outcome: Literal["succeeded", "failed", "rejected"],
        *,
        state: str,
        code: str | None = None,
        already_active: bool = False,
        correlation_id: str,
        causation_id: str,
    ) -> str:
        return await self._append(
            ACTIVATION_EVENT,
            outcome,
            handoff,
            provenance,
            ACTIVATION_TARGET,
            {"slot": handoff.slot, "state": state, "already_active": already_active, "code": code},
            correlation_id=correlation_id,
            causation_id=causation_id,
        )

    async def _append(
        self,
        event_type: str,
        outcome: str,
        handoff: GenesisHandoff,
        provenance: AuthorizationProvenance,
        target: tuple[str, str],
        metadata: dict[str, Any],
        *,
        correlation_id: str,
        causation_id: str,
    ) -> str:
        # One fact per authority, cause and outcome; an identical retry is ingested once.
        step = ":".join(
            (
                provenance.evidence_digest,
                causation_id,
                outcome,
                str(metadata.get("already_active", "")),
            )
        )
        event = LogEvent(
            event_id=stable_event_id(
                correlation_id=correlation_id, event_type=event_type, step=step
            ),
            event_type=event_type,
            outcome=outcome,
            subject={"resource_type": "agent", "resource_id": handoff.agent_id},
            target={"signal": target[0], "version": target[1]},
            correlation={
                "operation_id": correlation_id,
                "correlation_id": correlation_id,
                "causation_id": causation_id,
            },
            metadata=metadata,
        )
        result = await self._log.append(event, authorization=provenance)
        return result.record.event_id
