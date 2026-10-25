from __future__ import annotations

import logging
from types import SimpleNamespace
from uuid import uuid4

from dependency_injector import containers, providers

from src.base.dependencies.di_container import Container as BaseContainer
from src.domains.agentverse.agents.factory import AgentFactory
from src.domains.agentverse.agents.factory_v2 import AgentFactoryV2
from src.domains.agentverse.agents.lifecycle.agent_principal_resolver import (
    AgentPrincipalResolver as TrustedAgentPrincipalResolver,
)
from src.domains.agentverse.agents.lifecycle.agent_principal_resolver import (
    UnboundAgentSessionAuthority,
)
from src.domains.agentverse.agents.lifecycle.anubis import Anubis
from src.domains.agentverse.agents.lifecycle.khepri import Khepri
from src.domains.agentverse.agents.lifecycle.ncsc_connector import (
    AgentPrincipalResolver,
    SynapticExchangeBinding,
    TrustedNCSCConnector,
)
from src.domains.agentverse.agents.lifecycle.service import AgentLifecycleService
from src.domains.agentverse.agents.orchestrator.universal import UniversalOrchestrator
from src.domains.agentverse.dependencies.genesis_security import GenesisSecurityBindings
from src.domains.agentverse.ncsc.authorization import SynapticAuthorization
from src.domains.agentverse.ncsc.cpbx import SynapticExchange
from src.domains.agentverse.registries.mcp_tool_source import MCPToolSource
from src.domains.agentverse.registries.registries import (
    registries,
    synaptic_registry_instance,
    tool_registry_instance,
)
from src.domains.agentverse.repositories.run_report_repository import (
    RunReportRepository,
)
from src.domains.agentverse.runtime.instances import AgentInstanceRegistry
from src.domains.agentverse.runtime.instances_v2 import AgentInstanceRegistryV2
from src.domains.agentverse.runtime.presence import AgentPresenceStore
from src.domains.agentverse.security.revocation import RedisRevocationStore
from src.domains.agentverse.security.utu import UTUAuthority
from src.domains.agentverse.services.agent_chat_service import (
    AgentChatService,
)
from src.domains.agentverse.services.agent_service import AgentService
from src.domains.agentverse.services.audit_service import AuditService
from src.domains.agentverse.services.db_service import DBService
from src.domains.agentverse.services.divine_orchestration_service import (
    DivineOrchestrationService,
)
from src.domains.agentverse.services.pdf_ingestion_orchestration_service import (
    PDFIngestionOrchestrator,
)
from src.domains.agentverse.services.pdf_ingestion_service import (
    PDFIngestionService,
)
from src.domains.agentverse.services.personality_service import (
    PersonalityService,
)
from src.domains.agentverse.services.registry_service import (
    RegistryService,
)
from src.domains.agentverse.services.vectordb_service import (
    VectorDBService,
)
from src.domains.agentverse.services.workflow_service import (
    WorkflowService,
)
from src.domains.agentverse.tools.web_crawler_tool.dependencies.di_container import (
    WebCrawlerContainer,
)

logger = logging.getLogger(__name__)


class AgentverseContainer(containers.DeclarativeContainer):
    """
    Dependency injection container for AgentVerse.
    """

    # ------------------------------------------------------------------
    # External dependencies
    # ------------------------------------------------------------------

    mongodb_repository = providers.Dependency()
    mcp_proxy_client = providers.Dependency()
    file_service = providers.Dependency()
    browser_session_service = providers.Dependency()
    event_publisher = providers.Dependency()
    redis_repository = providers.Dependency()
    # One shared, default-deny snapshot. Bind trusted policy and a real session
    # authority before resolving Genesis services; V1 remains independent.
    genesis_sessions = providers.Singleton(UnboundAgentSessionAuthority)
    genesis_approvals = providers.Object({})
    synaptic_authorization = providers.Singleton(SynapticAuthorization)

    # ------------------------------------------------------------------
    # Core AgentVerse services
    # ------------------------------------------------------------------

    db_service = providers.Factory(
        DBService,
        mongodb_repository=mongodb_repository,
    )

    agent_identity_verifier = providers.Singleton(
        TrustedAgentPrincipalResolver,
        definitions=db_service,
        sessions=genesis_sessions,
        approvals=genesis_approvals,
        authorization=synaptic_authorization,
    )

    utu_revocations = providers.Singleton(RedisRevocationStore, repository=redis_repository)

    utu_authority = providers.Singleton(
        UTUAuthority,
        resolver=agent_identity_verifier,
        revocations=utu_revocations,
        authorization=synaptic_authorization,
    )

    agent_principal_resolver = utu_authority.provided.resolve_agent_principal

    synaptic_exchange = providers.Singleton(
        SynapticExchange,
        registry=providers.Object(synaptic_registry_instance),
        authorization=utu_authority,
    )

    run_report_repository = providers.Singleton(
        RunReportRepository,
        mongodb_repository=mongodb_repository,
    )

    orchestrator = providers.Singleton(
        UniversalOrchestrator,
    )

    # ------------------------------------------------------------------
    # Tool Containers
    # ------------------------------------------------------------------

    web_crawler = providers.Container(
        WebCrawlerContainer,
        mongodb_repository=mongodb_repository,
    )

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------

    workflow_service = providers.Factory(
        WorkflowService,
        db_service=db_service,
    )

    # ------------------------------------------------------------------
    # Agents
    # ------------------------------------------------------------------

    agent_factory = providers.Factory(
        AgentFactory,
        orchestrator=orchestrator,
        workflow_service=workflow_service,
    )

    personality_service = providers.Factory(
        PersonalityService,
    )

    registry_service = providers.Singleton(
        RegistryService,
        registries=providers.Object(registries),
    )

    # ------------------------------------------------------------------
    # MCP
    # ------------------------------------------------------------------

    mcp_tool_source = providers.Singleton(
        MCPToolSource,
        mcp_client=mcp_proxy_client,
        tool_registry=providers.Object(tool_registry_instance),
        file_service=file_service,
    )

    # ------------------------------------------------------------------
    # Other services
    # ------------------------------------------------------------------

    audit_service = providers.Factory(
        AuditService,
        db_service=db_service,
    )

    agent_service = providers.Factory(
        AgentService,
        agent_factory=agent_factory,
        db_service=db_service,
        audit_service=audit_service,
        workflow_service=workflow_service,
    )

    vectordb_service = providers.Factory(
        VectorDBService,
        db_service=db_service,
    )

    pdf_ingestion_service = providers.Factory(
        PDFIngestionService,
    )

    pdf_ingestion_orchestrator_service = providers.Factory(
        PDFIngestionOrchestrator,
        db_service=db_service,
        vectordb_service=vectordb_service,
        pdf_ingestion_service=pdf_ingestion_service,
    )

    instance_registry = providers.Singleton(
        AgentInstanceRegistry,
    )

    runtime_id = providers.Singleton(
        lambda: uuid4().hex,
    )

    instance_registry_v2 = providers.Singleton(
        AgentInstanceRegistryV2,
    )

    agent_presence_store = providers.Singleton(
        AgentPresenceStore,
        repository=redis_repository,
        lease_ttl=60,
    )

    agent_chat_service = providers.Factory(
        AgentChatService,
        agent_service=agent_service,
        instance_registry=instance_registry,
        file_service=file_service,
        run_report_repository=run_report_repository,
    )

    trusted_ncsc_connector = providers.Singleton(
        TrustedNCSCConnector,
        exchange=synaptic_exchange,
        resolve_principal=agent_principal_resolver,
    )

    agent_factory_v2 = providers.Factory(
        AgentFactoryV2,
        ncsc_connector=trusted_ncsc_connector,
    )

    agent_lifecycle_service = providers.Singleton(
        AgentLifecycleService,
        instances=instance_registry_v2,
        presence=agent_presence_store,
        factory=agent_factory_v2,
        runtime_id=runtime_id,
        authority=utu_authority,
    )

    khepri = providers.Singleton(
        Khepri,
        lifecycle=agent_lifecycle_service,
    )

    anubis = providers.Singleton(
        Anubis,
        lifecycle=agent_lifecycle_service,
    )

    divine_orchestration_service = providers.Factory(
        DivineOrchestrationService,
        agent_service=agent_service,
        agent_chat_service=agent_chat_service,
    )


def extend_container(
    base_container: BaseContainer,
    *,
    genesis_security: GenesisSecurityBindings | None = None,
    synaptic_exchange: SynapticExchangeBinding | providers.Provider | None = None,
    agent_principal_resolver: AgentPrincipalResolver | providers.Provider | None = None,
) -> BaseContainer:
    """Attach V1 services and lazy CAOS Genesis providers.

    GenesisSecurityBindings configures the default resolver and exchange with
    the same immutable authorization snapshot. Without it activation denies.
    The one-argument V1 call remains supported. The trusted composition root
    supplies the exchange and async principal resolver as concrete objects or
    dependency-injector providers. No base-container location is assumed.

    Concrete callables are wrapped as objects so DI passes the resolver itself
    to TrustedNCSCConnector; it must be invoked with an AgentInstanceKey only
    when a runtime connects. A provider must likewise return the resolver,
    rather than execute principal resolution during service construction.

    V2 namespace attributes are providers: call ``agentverse.khepri()`` or
    ``agentverse.anubis()`` to resolve the supervisors. Their shared lifecycle
    service is a singleton, as are the V2 registry, presence store, and runtime
    ID. Missing security bindings deny activation without affecting V1.

    Bind before first resolving V2 services. For deferred configuration, use
    ``agentverse.synaptic_exchange.override(exchange_provider)`` and
    ``agentverse.agent_principal_resolver.override(
        providers.Object(resolve_principal)
    )``. Custom resolvers apply only to the trusted connector; lifecycle caller checks
    always use UTU's independent verifier and require Genesis session bindings.
    Application shutdown should await the resolved lifecycle's ``shutdown()``.
    """
    av = AgentverseContainer()
    if genesis_security is not None:
        if synaptic_exchange is not None or agent_principal_resolver is not None:
            raise ValueError("Use Genesis security bindings or custom exchange/resolver bindings")
        av.genesis_sessions.override(providers.Object(genesis_security.sessions))
        av.genesis_approvals.override(providers.Object(dict(genesis_security.approvals)))
        av.synaptic_authorization.override(providers.Object(genesis_security.authorization))

    # Keep provider bindings lazy. In particular, never call an exchange or
    # principal-resolver provider here: it may have unresolved dependencies.
    if synaptic_exchange is not None:
        av.synaptic_exchange.override(
            synaptic_exchange
            if isinstance(synaptic_exchange, providers.Provider)
            else providers.Object(synaptic_exchange)
        )

    if agent_principal_resolver is not None:
        av.agent_principal_resolver.override(
            agent_principal_resolver
            if isinstance(agent_principal_resolver, providers.Provider)
            else providers.Object(agent_principal_resolver)
        )

    # ------------------------------------------------------------------
    # External dependency bindings
    # ------------------------------------------------------------------

    av.browser_session_service.override(base_container.browser_session.browser_session_service)

    av.mongodb_repository.override(base_container.database.mongodb_repository)

    av.redis_repository.override(base_container.redis.redis_repository)
    av.mcp_proxy_client.override(base_container.http.mcp_proxy_client)

    av.file_service.override(base_container.blob_storage.file_service)

    av.event_publisher.override(base_container.events.domain_event_publisher)

    # ------------------------------------------------------------------
    # Wiring
    # ------------------------------------------------------------------

    av.wire(
        modules=[
            "src.domains.agentverse.dependencies.get_db_service",
            "src.domains.agentverse.dependencies.get_agent_service",
            "src.domains.agentverse.dependencies.get_workflow_service",
            "src.domains.agentverse.dependencies.get_agent_chat_service",
            "src.domains.agentverse.dependencies.get_file_service",
            "src.domains.agentverse.dependencies.get_instance_registry",
            "src.domains.agentverse.dependencies.get_divine_orchestration_service",
            "src.domains.agentverse.dependencies.get_registry_service",
            "src.domains.agentverse.dependencies.get_pdf_ingestion_orchestration_service",
            "src.domains.agentverse.dependencies.get_audit_service",
            "src.domains.agentverse.interfaces.api.v1.workflow_routes",
            "src.domains.agentverse.interfaces.api.v1.interface",
            "src.domains.agentverse.interfaces.api.v1.file_routes",
            "src.domains.agentverse.services.divine_orchestration_service",
        ]
    )

    # ------------------------------------------------------------------
    # AgentVerse namespace
    # ------------------------------------------------------------------

    if not hasattr(base_container, "agentverse"):
        base_container.agentverse = SimpleNamespace()

    ns = base_container.agentverse

    ns.db_service = av.db_service
    ns.agent_service = av.agent_service
    ns.workflow_service = av.workflow_service
    ns.divine_orchestration_service = av.divine_orchestration_service
    ns.vectordb_service = av.vectordb_service
    ns.pdf_ingestion_service = av.pdf_ingestion_service
    ns.pdf_ingestion_orchestrator_service = av.pdf_ingestion_orchestrator_service
    ns.personality_service = av.personality_service
    ns.registry_service = av.registry_service

    # Preserve the V1 concrete registry API.
    ns.instance_registry = av.instance_registry()

    # CAOS Genesis exposures remain providers. Resolving a supervisor creates
    # the shared lifecycle coordinator, but does not activate an agent.
    ns.runtime_id = av.runtime_id
    ns.instance_registry_v2 = av.instance_registry_v2
    ns.agent_presence_store = av.agent_presence_store
    ns.genesis_sessions = av.genesis_sessions
    ns.genesis_approvals = av.genesis_approvals
    ns.synaptic_authorization = av.synaptic_authorization
    ns.synaptic_exchange = av.synaptic_exchange
    ns.utu_authority = av.utu_authority
    ns.agent_principal_resolver = av.agent_principal_resolver
    ns.trusted_ncsc_connector = av.trusted_ncsc_connector
    ns.agent_factory_v2 = av.agent_factory_v2
    ns.agent_lifecycle_service = av.agent_lifecycle_service
    ns.khepri = av.khepri
    ns.anubis = av.anubis

    ns.agent_chat_service = av.agent_chat_service
    ns.audit_service = av.audit_service
    ns.mcp_tool_source = av.mcp_tool_source
    ns.file_service = av.file_service
    ns.web_crawler = av.web_crawler

    # ------------------------------------------------------------------
    # Backwards-compatible aliases
    # ------------------------------------------------------------------

    base_container.agent_service = av.agent_service
    base_container.registry_service = av.registry_service

    return base_container
