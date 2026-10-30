"""Opt-in application composition. Configuration is trusted, never request data."""

from __future__ import annotations

import inspect
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI

if TYPE_CHECKING:
    from src.base.dependencies.di_container import Container

from src.domains.agentverse.agents.genesis.bootstrap import GenesisBootstrap
from src.domains.agentverse.agents.genesis.initiation import GenesisHandoff
from src.domains.agentverse.agents.lifecycle.agent_principal_resolver import (
    AgentActivationApproval,
    SystemCitizen,
)
from src.domains.agentverse.dependencies.genesis_security import GenesisSecurityBindings
from src.domains.agentverse.ncsc.authorization import SynapticAuthorization
from src.domains.agentverse.ncsc.models import SynapticGrants, TrustedLine
from src.domains.agentverse.security.sessions import RedisAgentSessionAuthority
from src.domains.agentverse.security.validation_gateway import ValidationGatewayBinding
from src.domains.agentverse.tools.store_tool.models import ProvisionedIdentity


class _RedisBinding:
    """Resolve the existing DI repository after its async resource is ready."""

    def __init__(self, provider: Callable[[], Any]) -> None:
        self._provider = provider

    async def eval(self, script: str, keys: list[str], args: list[Any]) -> Any:
        repository = self._provider()
        if inspect.isawaitable(repository):
            repository = await repository
        return await repository.eval(script, keys=keys, args=args)


def build_genesis_security(container: Container) -> dict[str, GenesisSecurityBindings]:
    if os.environ.get("GENESIS_SESSIONS_ENABLED", "false").lower() != "true":
        return {}
    # An optional server-owned JSON file supplies a deployment policy snapshot.
    # Loading does not provision tenant state in Redis.
    path = os.environ.get("GENESIS_SECURITY_POLICY_FILE")
    config = json.loads(Path(path).read_text()) if path else {}
    approvals = {}
    for item in config.get("approvals", []):
        approval = AgentActivationApproval(**item)
        if type(approval.enabled) is not bool:
            raise ValueError("Genesis approval enabled must be boolean")
        scope = (approval.tenant_id, approval.agent_id)
        if scope in approvals:
            raise ValueError("Duplicate Genesis approval")
        approvals[scope] = approval
    grants = {}
    for item in config.get("grants", []):
        scope = (item["tenant_id"], item["agent_id"])
        if scope in grants or any(not isinstance(v, str) or not v for v in scope):
            raise ValueError("Invalid or duplicate Genesis grant scope")
        invoke = item["invoke"]
        if not isinstance(invoke, list) or any(not isinstance(v, str) or not v for v in invoke):
            raise ValueError("Genesis invoke grants must be a list of exact signals")
        grants[scope] = SynapticGrants(invoke)
    provisions = _provisions(config.get("provisioned", []), approvals)
    handoffs = _handoffs(config.get("handoffs", []))
    citizens = _citizens(config.get("system_citizens", []), approvals, provisions, handoffs)
    bootstrap = _bootstrap(config.get("bootstrap", []), provisions, handoffs, citizens)
    sessions = RedisAgentSessionAuthority(
        repository=_RedisBinding(container.redis.redis_repository),
        jwt_service=container.services.jwt_service(),
    )
    return {
        "genesis_security": GenesisSecurityBindings(
            sessions=sessions,
            approvals=approvals,
            provisions=provisions,
            handoffs=handoffs,
            citizens=citizens,
            bootstrap=bootstrap,
            validation_gateways=tuple(
                ValidationGatewayBinding.model_validate(item)
                for item in config.get("validation_gateways", [])
            ),
            creation_gateways=tuple(
                ValidationGatewayBinding.model_validate(item)
                for item in config.get("creation_gateways", [])
            ),
            authorization=SynapticAuthorization(
                grants=grants,
                trusted_lines=[TrustedLine(**item) for item in config.get("trusted_lines", [])],
            ),
        )
    }


def _handoffs(items: Any) -> tuple[GenesisHandoff, ...]:
    if not isinstance(items, list):
        raise TypeError("Genesis handoffs must be a list")
    handoffs = tuple(GenesisHandoff.model_validate(item) for item in items)
    scopes = {(h.tenant_id, h.initiator_id, h.subject_id) for h in handoffs}
    if len(scopes) != len(handoffs):
        raise ValueError("Duplicate Genesis handoff")
    return handoffs


def _bootstrap(
    items: Any,
    provisions: dict[str, ProvisionedIdentity],
    handoffs: tuple[GenesisHandoff, ...],
    citizens: dict[tuple[str, str], SystemCitizen],
) -> tuple[GenesisBootstrap, ...]:
    """Each entry leases exactly one handoff's two System Citizens, never an agent."""
    if not isinstance(items, list):
        raise TypeError("Genesis bootstrap must be a list")
    entries = tuple(GenesisBootstrap.model_validate(item) for item in items)
    if len({(e.tenant_id, e.slot) for e in entries}) != len(entries):
        raise ValueError("Duplicate Genesis bootstrap")
    for entry in entries:
        handoff = next(
            (h for h in handoffs if (h.tenant_id, h.slot) == (entry.tenant_id, entry.slot)), None
        )
        if handoff is None or entry.slot not in provisions:
            raise ValueError("Genesis bootstrap requires its handoff and provisioned identity")
        entry.check(handoff, provisions[entry.slot])
        if any((entry.tenant_id, agent) not in citizens for agent in entry.agents):
            raise ValueError("Genesis bootstrap may lease only System Citizens")
    return entries


def _citizens(
    items: Any,
    approvals: dict[tuple[str, str], AgentActivationApproval],
    provisions: dict[str, ProvisionedIdentity],
    handoffs: tuple[GenesisHandoff, ...],
) -> dict[tuple[str, str], SystemCitizen]:
    """Deterministic system principals; identity evidence only, never a grant.

    Each needs a reviewed approval in the same scope. A provisioned or handed-off
    target is a Cognitive Agent definition and can never be declared a citizen.
    """
    if not isinstance(items, list):
        raise TypeError("System citizens must be a list")
    agents = {(p.tenant_id, p.agent_id) for p in provisions.values()}
    agents |= {(h.tenant_id, h.agent_id) for h in handoffs}
    citizens: dict[tuple[str, str], SystemCitizen] = {}
    for item in items:
        if not isinstance(item, dict) or set(item) != {"tenant_id", "agent_id", "name"}:
            raise ValueError("System citizen requires exactly tenant_id, agent_id and name")
        citizen = SystemCitizen(**item)
        scope = (citizen.tenant_id, citizen.agent_id)
        if scope in citizens:
            raise ValueError("Duplicate system citizen")
        if scope not in approvals:
            raise ValueError("System citizen requires a reviewed approval")
        if scope in agents:
            raise ValueError("A provisioned Cognitive Agent cannot be a system citizen")
        citizens[scope] = citizen
    return citizens


def _provisions(
    items: Any, approvals: dict[tuple[str, str], AgentActivationApproval]
) -> dict[str, ProvisionedIdentity]:
    """Strict policy-pinned identities. Each authorizes one principal for one agent ID."""
    if not isinstance(items, list):
        raise TypeError("Genesis provisioned identities must be a list")
    provisions: dict[str, ProvisionedIdentity] = {}
    agent_ids: set[tuple[str, str]] = set()
    for item in items:
        entry = ProvisionedIdentity.model_validate(item)
        scope = (entry.tenant_id, entry.agent_id)
        if entry.slot in provisions or scope in agent_ids:
            raise ValueError("Duplicate Genesis provisioned slot or agent ID")
        if entry.agent_id == entry.principal_id:
            raise ValueError("A provisioned identity cannot be its own provisioner")
        approval = approvals.get(scope)
        # A pre-configured activation approval must belong to the same owner the
        # store will record as creator, or the agent could never be activated.
        if approval is not None and approval.owner_user_id != entry.subject_id:
            raise ValueError("Provisioned subject does not match the activation approval owner")
        provisions[entry.slot] = entry
        agent_ids.add(scope)
    return provisions


def configure_genesis_transport(app: FastAPI, bindings: GenesisSecurityBindings) -> None:
    # Other application-supplied adapters retain their established transport.
    if not isinstance(bindings.sessions, RedisAgentSessionAuthority):
        return
    sessions = bindings.sessions
    app.state.genesis_sessions = sessions

    async def start() -> None:
        from src.domains.agentverse.registries import synaptic_registry_instance
        from src.domains.agentverse.services.cognitive_response_v2 import (
            SIGNAL,
            CognitiveResponseV2,
        )

        av = app.container.agentverse
        av.utu_authority()
        if any(
            "chat.response" in grant.invoke for grant in bindings.authorization._grants.values()
        ):
            responder = CognitiveResponseV2(
                definitions=av.db_service(),
                authority=av.utu_authority(),
                history=av.client_event_dispatcher_v2().history,
                client_factory=lambda: getattr(app.state, "client_factory", None),
            )
            synaptic_registry_instance.subscribe(SIGNAL, provider="cognitive.chat")(
                responder.handle
            )
        from src.domains.agentverse.tools.store_tool import register_store_create
        from src.domains.agentverse.tools.store_tool.spec import STORE_CREATE_SIGNAL

        policy = bindings.authorization
        # Descriptor registration is independent of provisioning or execution grants.
        from src.domains.agentverse.manuscripts.capability import SIGNALS as MANUSCRIPT_SIGNALS
        from src.domains.agentverse.tools.manuscript_tool import validate_manuscript_bindings

        manuscript_enabled = any(
            signal in grant.invoke
            for grant in policy._grants.values()
            for signal in MANUSCRIPT_SIGNALS.values()
        )
        if any(STORE_CREATE_SIGNAL in grant.invoke for grant in policy._grants.values()) or any(
            line.target == STORE_CREATE_SIGNAL for line in policy._trusted_lines
        ):
            register_store_create(synaptic_registry_instance, av.store().tool())
        from src.domains.agentverse.tools.store_tool import (
            ProvisionedStoreCapability,
            register_store_create_provisioned,
        )
        from src.domains.agentverse.tools.store_tool.spec import STORE_CREATE_PROVISIONED_SIGNAL

        if bindings.provisions and (
            any(
                STORE_CREATE_PROVISIONED_SIGNAL in grant.invoke for grant in policy._grants.values()
            )
            or any(line.target == STORE_CREATE_PROVISIONED_SIGNAL for line in policy._trusted_lines)
        ):
            register_store_create_provisioned(
                synaptic_registry_instance,
                ProvisionedStoreCapability(tool=av.store().tool(), provisions=bindings.provisions),
            )
        from src.domains.agentverse.tools.validation_tool import register_data_validation
        from src.domains.agentverse.tools.validation_tool.spec import DATA_VALIDATION_SIGNAL

        if any(DATA_VALIDATION_SIGNAL in grant.invoke for grant in policy._grants.values()) or any(
            line.target == DATA_VALIDATION_SIGNAL for line in policy._trusted_lines
        ):
            register_data_validation(synaptic_registry_instance, av.validation().tool())
        if bindings.validation_gateways:
            from src.domains.agentverse.agents.composition import AgentComposer
            from src.domains.agentverse.security.validation_gateway import (
                AgentDraftValidationGateway,
            )

            app.state.agent_draft_validation_gateway = AgentDraftValidationGateway(
                bindings=bindings.validation_gateways,
                authority=av.utu_authority(),
                exchange=av.synaptic_exchange(),
                read_definition=av.db_service().find_by_id,
                composer=AgentComposer(catalog=lambda: getattr(app.state, "client_factory", None)),
            )
            from src.domains.agentverse.agents.creation_review.service import (
                DraftReviewApprovalService,
            )

            app.state.agent_draft_review_approvals = DraftReviewApprovalService(
                gateway=app.state.agent_draft_validation_gateway,
                repository=av.draft_review_approval_repository(),
                audit=av.caos_log_service(),
            )
            from src.domains.agentverse.agents.creation_drafts.service import AgentDraftService

            # Drafts reuse the gateway's human-to-source authority; no grant or signal.
            app.state.agent_drafts = AgentDraftService(
                gateway=app.state.agent_draft_validation_gateway,
                repository=av.agent_draft_repository(),
            )
            from src.domains.agentverse.manuscripts.catalog import RegistryManuscriptCatalog
            from src.domains.agentverse.manuscripts.definitions import register_builtin
            from src.domains.agentverse.manuscripts.registry import manuscript_registry_instance
            from src.domains.agentverse.manuscripts.service import ManuscriptService
            from src.domains.agentverse.registries.registries import (
                registries as catalog_registries,
            )

            # Published Manuscripts (fingerprint-pinned) and the deterministic runtime.
            # Reused by the scoped CPBX provider; never exposed through V1 execution.
            if not manuscript_registry_instance.frozen:
                register_builtin(manuscript_registry_instance)
                manuscript_registry_instance.freeze()
            app.state.manuscripts = ManuscriptService(
                registry=manuscript_registry_instance,
                repository=av.manuscript_session_repository(),
                catalog=RegistryManuscriptCatalog(
                    registries=catalog_registries, workflow_service=av.workflow_service()
                ),
            )
            # Enki's governed Manuscript signals, registered only when the reviewed policy
            # grants one (MANUSCRIPT-0.1A); visibility follows the validation gateways.
            if manuscript_enabled:
                from src.domains.agentverse.manuscripts.capability import (
                    ManuscriptCapability,
                    gateway_visibility,
                    register_manuscript_capability,
                )

                register_manuscript_capability(
                    synaptic_registry_instance,
                    ManuscriptCapability(
                        authority=av.utu_authority(),
                        manuscripts=app.state.manuscripts,
                        visible=gateway_visibility(bindings.validation_gateways),
                    ),
                )
            from src.domains.agentverse.agents.creation_drafts.lifecycle import (
                AgentDraftLifecycleService,
            )

            # Exact-revision review, approval binding, reopen and confirmed consumption,
            # composed from the unchanged G3.3 approvals and G3.4 creation records.
            app.state.agent_draft_lifecycle = AgentDraftLifecycleService(
                drafts=app.state.agent_drafts,
                revisions=av.agent_draft_repository(),
                gateway=app.state.agent_draft_validation_gateway,
                approvals=app.state.agent_draft_review_approvals,
                creations=av.agent_creation_repository(),
            )
            from src.domains.agentverse.agents.creation_drafts.proposal_service import (
                AgentDraftProposalService,
            )

            # Human decisions on proposals; proposals arrive only through the scoped
            # agent.draft.propose capability below.
            app.state.agent_draft_proposals = AgentDraftProposalService(
                drafts=app.state.agent_drafts,
                revisions=av.agent_draft_repository(),
                proposals=av.agent_draft_proposal_repository(),
                audit=av.caos_log_service(),
            )
            from src.domains.agentverse.agents.creation_drafts.proposals import PROPOSE_SIGNAL

            # Registered only when the reviewed policy grants it (Enki, DRAFT-0.2A).
            if any(PROPOSE_SIGNAL in grant.invoke for grant in policy._grants.values()):
                from src.domains.agentverse.agents.creation_drafts.capability import (
                    DraftProposalCapability,
                    register_draft_propose,
                )

                register_draft_propose(
                    synaptic_registry_instance,
                    DraftProposalCapability(
                        authority=av.utu_authority(),
                        proposals=app.state.agent_draft_proposals,
                    ),
                )
            from src.domains.agentverse.tools.store_tool.spec import STORE_CREATE_APPROVED_SIGNAL

            if bindings.creation_gateways and any(
                STORE_CREATE_APPROVED_SIGNAL in grant.invoke for grant in policy._grants.values()
            ):
                from src.domains.agentverse.agents.creation_review.creation import (
                    AgentCreationService,
                    ApprovedStoreCapability,
                    register_store_create_approved,
                )
                from src.domains.agentverse.agents.genesis import GenesisProvisioner

                register_store_create_approved(
                    synaptic_registry_instance,
                    ApprovedStoreCapability(
                        tool=av.store().tool(), approvals=app.state.agent_draft_review_approvals
                    ),
                )
                app.state.agent_creation = AgentCreationService(
                    bindings=bindings.creation_gateways,
                    authority=av.utu_authority(),
                    exchange=av.synaptic_exchange(),
                    gateway=app.state.agent_draft_validation_gateway,
                    approvals=app.state.agent_draft_review_approvals,
                    provisioner=GenesisProvisioner(
                        operations=app.container.operations.managed_operation_repository_v2(),
                        log=av.caos_log_service(),
                        composer=AgentComposer(
                            catalog=lambda: getattr(app.state, "client_factory", None)
                        ),
                        authority=av.utu_authority(),
                    ),
                    repository=av.agent_creation_repository(),
                    audit=av.caos_log_service(),
                    read_definition=av.db_service().find_by_id,
                )
        # Validate before local initialization can provision anything. Enabled policy
        # without a gateway/provider, or with mismatched bindings, cannot start.
        if manuscript_enabled:
            validate_manuscript_bindings(synaptic_registry_instance)
        if os.getenv("GENESIS_LOCAL_ENABLED") == "true":
            from src.domains.agentverse.dependencies.genesis_local import initialize_local

            await initialize_local(app.container)
            if bindings.handoffs:
                from src.domains.agentverse.agents.composition import AgentComposer
                from src.domains.agentverse.agents.genesis import GenesisProvisioner
                from src.domains.agentverse.agents.genesis.initiation import (
                    GenesisInitiation,
                    register_genesis_initiation,
                )

                log = av.caos_log_service()
                provisioner = GenesisProvisioner(
                    operations=app.container.operations.managed_operation_repository_v2(),
                    log=log,
                    composer=AgentComposer(
                        catalog=lambda: getattr(app.state, "client_factory", None)
                    ),
                    authority=av.utu_authority(),
                )
                from src.domains.agentverse.agents.genesis.activation import GenesisActivation

                # Khepri activation is its own request (DEV-0.2); creation reaches it only
                # for a handoff whose reviewed policy explicitly opts in.
                app.state.genesis_activation = GenesisActivation(
                    khepri=av.khepri(),
                    instances=av.instance_registry_v2(),
                    authority=av.utu_authority(),
                    definitions=av.db_service(),
                    catalog=lambda: getattr(app.state, "client_factory", None),
                    log=log,
                    handoffs=bindings.handoffs,
                )
                activation = (
                    app.state.genesis_activation
                    if any(h.activation != "none" for h in bindings.handoffs)
                    else None
                )
                register_genesis_initiation(
                    synaptic_registry_instance,
                    GenesisInitiation(
                        handoffs=bindings.handoffs,
                        provisions=bindings.provisions,
                        authority=av.utu_authority(),
                        exchange=av.synaptic_exchange(),
                        provisioner=provisioner,
                        log=log,
                        activation=activation,
                    ),
                )
                if len(bindings.bootstrap) > 1:
                    raise ValueError("Local Genesis allows exactly one bootstrap lease")
                for entry in bindings.bootstrap:
                    from src.domains.agentverse.agents.genesis.bootstrap import EnkiBootstrap

                    handoff = next(h for h in bindings.handoffs if h.slot == entry.slot)
                    # Runs after the model catalog exists (composition needs it).
                    app.state.bootstrap_genesis = EnkiBootstrap(
                        entry=entry,
                        handoff=handoff,
                        provision=bindings.provisions[entry.slot],
                        definitions=av.db_service(),
                        sessions=sessions,
                        authority=av.utu_authority(),
                        exchange=av.synaptic_exchange(),
                        log=log,
                    ).ensure
        await sessions.start()

    app.state.start_genesis_sessions = start
    app.state.close_genesis_sessions = sessions.close
