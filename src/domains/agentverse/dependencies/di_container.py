from __future__ import annotations

import logging
from types import SimpleNamespace

from dependency_injector import containers, providers

from src.base.dependencies.di_container import Container as BaseContainer
from src.domains.agentverse.agents.factory import AgentFactory
from src.domains.agentverse.agents.orchestrator.universal import UniversalOrchestrator
from src.domains.agentverse.registries.mcp_tool_source import MCPToolSource
from src.domains.agentverse.registries.registries import (
    registries,
    tool_registry_instance,
)
from src.domains.agentverse.repositories.run_report_repository import (
    RunReportRepository,
)
from src.domains.agentverse.runtime.instances import AgentInstanceRegistry
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

    # ------------------------------------------------------------------
    # Core AgentVerse services
    # ------------------------------------------------------------------

    db_service = providers.Factory(
        DBService,
        mongodb_repository=mongodb_repository,
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

    agent_chat_service = providers.Factory(
        AgentChatService,
        agent_service=agent_service,
        instance_registry=instance_registry,
        file_service=file_service,
        run_report_repository=run_report_repository,
    )

    divine_orchestration_service = providers.Factory(
        DivineOrchestrationService,
        agent_service=agent_service,
        agent_chat_service=agent_chat_service,
    )


def extend_container(
    base_container: BaseContainer,
) -> BaseContainer:
    av = AgentverseContainer()

    # ------------------------------------------------------------------
    # External dependency bindings
    # ------------------------------------------------------------------

    av.browser_session_service.override(
        base_container.browser_session.browser_session_service
    )

    av.mongodb_repository.override(
        base_container.database.mongodb_repository
    )

    av.mcp_proxy_client.override(
        base_container.http.mcp_proxy_client
    )

    av.file_service.override(
        base_container.blob_storage.file_service
    )

    av.event_publisher.override(
        base_container.events.domain_event_publisher
    )

    crawler = av.web_crawler()

    factory_a = crawler.acquisition_context_factory()
    factory_b = crawler.acquisition_context_factory()

    assert factory_a is factory_b

    assert factory_a.asset_store is crawler.asset_state_store()
    assert (
        factory_a.sitemap_url_inventory_store
        is crawler.sitemap_url_inventory_store()
    )
    assert (
        factory_a.crawl_run_report_store
        is crawler.crawl_run_report_store()
    )
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
    ns.pdf_ingestion_orchestrator_service = (
        av.pdf_ingestion_orchestrator_service
    )
    ns.personality_service = av.personality_service
    ns.registry_service = av.registry_service

    ns.instance_registry = av.instance_registry()

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