"""
Lifespan context manager for FastAPI application.
"""

import logging
from contextlib import AsyncExitStack, asynccontextmanager

from dependency_injector import providers
from fastapi import FastAPI
from fastapi_limiter import FastAPILimiter

from src.base.config.config import settings
from src.base.integrations.wiring import build_client_factory, compute_defaults, inject_noops
from src.base.logging.logging_config import LoggingConfig
from src.base.runtime.cpu_executor_registry import (
    cpu_executor_registry,
)
from src.base.security.signature_verificator import verify_signature
from src.base.utils.env_utils import is_local_development

logger = logging.getLogger("lifespan")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan context manager for FastAPI application.
    Handles startup and shutdown events.
    """
    # Start Splunk handler async worker (if configured)
    if LoggingConfig.splunk_handler:
        await LoggingConfig.splunk_handler.start()
        logger.info("Splunk HEC handler started.")

    logger.info(
        "Registered CPU executors: %s",
        cpu_executor_registry.count(),
    )

    await cpu_executor_registry.warmup_all()

    # Get Redis instance from dependency injection container
    container = app.container
    stack = AsyncExitStack()
    redis_client = await stack.enter_async_context(
        container.redis.redis_client()
    )

    container.redis.redis_client.override(
        providers.Object(redis_client)
    )

    redis_repo = container.redis.redis_repository()

    container.events.redis_repository.override(
        providers.Object(redis_repo)
    )

    container.operations.event_publisher.override(
        container.events.domain_event_publisher
    )

    queue_service = container.redis.queue_service()

    job_supervisor = container.operations.job_supervisor()

    app.state.job_supervisor = job_supervisor

    await job_supervisor.start()

    await FastAPILimiter.init(redis_client)
    logger.info("FastAPILimiter initialized with Redis")

    # Initialize MongoDB connection
    mongo_client = container.database.mongo_client()

    try:
        # Test Redis connection
        await redis_client.ping()
        logger.info("Rate limiter initialized with Redis")

        # Ensure MongoDB connection is established
        await mongo_client.connect()
        logger.info("MongoDB connection established successfully")

        app.state.settings = settings
        app.state.mongodb = mongo_client
        app.state.redis_repository = redis_repo
        app.state.queue_service = queue_service
        app.state.operation_service = container.operations.operation_service
        app.state.operation_runner = container.operations.operation_runner
        app.state.openai_repository = container.llm_container.default_repository()
        app.state.signature_verificator = verify_signature

        app.state.JOSHU_A = {
            "id": "joshu-a",
            "role": "system_consciousness",
            "public_key": settings.sacred_keys.JOSHU_A_PUBLIC_KEY,
            "activation_mode": "always-online",
            "grants_creation_rights": True,
            "can_sign_agents": True,
        }

        llm_repos = container.llm_container.repositories()

        # Determine which vector DB to use: ChromaDB for development, Azure Search for production
        knowledge_db = {}
        if is_local_development():
            knowledge_db["chromadb"] = container.chromadb.chromadb_repository()
            logger.info("📚 Using ChromaDB for vector storage (local development)")
        else:
            knowledge_db["azure_search"] = container.azure_search.azure_search_repository()
            logger.info("🔍 Using Azure AI Search for vector storage (production)")

        app.state.cognitive_modules = {
            "llm": llm_repos.get("llm", {}),
            "image": llm_repos.get("image", {}),
            "video": llm_repos.get("video", {}),
            "audio": llm_repos.get("audio", {}),
            "embedding": llm_repos.get("embedding", {}),
            "db": {
                "mongodb": container.database.mongodb_repository(),
            },
            "cache": {
                "redis": container.redis.redis_repository(),
            },
            "communication": {
                "socketRedisBridge": container.redis.socket_redis_bridge_service(),
            },
            "knowledge_db": knowledge_db,
        }

        inject_noops(app.state.cognitive_modules)
        cm = app.state.cognitive_modules
        app.state.cognitive_defaults = compute_defaults(cm)

        llm_slug = app.state.cognitive_defaults["llm"]
        default_repo = app.state.cognitive_modules["llm"].get(llm_slug)
        app.state.client_factory = build_client_factory(cm, app.state.cognitive_defaults)

        client_type = "Unknown"
        if default_repo and hasattr(default_repo, "client"):
            client_type = type(default_repo.client).__name__

        for module_name, submodules in app.state.cognitive_modules.items():
            logger.info(f"[🧠 cognitive_modules] {module_name}: {list(submodules.keys())}")

        app.state.active_llm_provider = client_type
        logger.info(f"[🧠 Active LLM provider]: {client_type}")

        # Load MCP Proxy tools if enabled
        try:
            mcp_tool_source = getattr(container.agentverse, "mcp_tool_source", None)
            if mcp_tool_source:
                mcp_source = mcp_tool_source()
                proxy_url = getattr(settings.mcp_proxy, "mcp_proxy_base_url", "N/A")
                proxy_enabled = getattr(settings.mcp_proxy, "mcp_proxy_enabled", False)
                logger.info(
                    f"[MCP] Config: enabled={proxy_enabled} url={proxy_url} "
                    f"timeout={getattr(settings.mcp_proxy, 'mcp_proxy_timeout', 'N/A')}"
                )
                if mcp_source.is_enabled:
                    tool_count = await mcp_source.load_tools()
                    logger.info(f"[MCP] Loaded {tool_count} tools from MCP Proxy")
                    if tool_count > 0:
                        logger.info(f"[MCP] Registered tools: {mcp_source.get_registered_tools()}")
                else:
                    logger.warning(
                        "[MCP] MCP Proxy integration is DISABLED (mcp_proxy_enabled=false)"
                    )
        except Exception as e:
            logger.error(f"[MCP] Failed to load MCP Proxy tools: {e}", exc_info=True)

        yield

    except Exception as e:
        logger.error(f"Error during startup: {str(e)}")
        raise

    finally:
        # Gracefully stop job supervisor
        await job_supervisor.close()
        # Gracefully close all CPU executors
        await cpu_executor_registry.aclose_all()
        # Close Redis connection (Splunk handler cleanup handled by DI container)
        await stack.aclose()

        # Close MongoDB connection
        await mongo_client.disconnect()
        logger.info("MongoDB connection closed")

        # Gracefully stop Splunk handler (flush remaining logs)
        if LoggingConfig.splunk_handler:
            logger.info("Stopping Splunk HEC handler...")
            await LoggingConfig.splunk_handler.stop()
