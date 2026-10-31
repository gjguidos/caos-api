"""
Lifespan context manager for FastAPI application.
"""

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
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
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Own cleanup before startup, preserving the original failure if teardown fails."""
    cleanups: dict[str, Callable[[], Awaitable[None]]] = {}
    failed = False
    try:
        async with _startup_lifespan(app, cleanups):
            yield
    except BaseException:
        failed = True
        raise
    finally:
        cleanup_errors: list[BaseException] = []
        # Quiesce producers/consumers before closing shared infrastructure.
        for name in (
            "genesis",
            "genesis_sessions",
            "supervisor",
            "runtime",
            "rabbitmq",
            "executors",
            "mongo",
            "redis",
            "splunk",
        ):
            cleanup = cleanups.get(name)
            if cleanup is None:
                continue
            try:
                await cleanup()
            except BaseException as error:
                cleanup_errors.append(error)
                logger.exception("Failed cleaning up %s", name)
        if cleanup_errors and not failed:
            raise cleanup_errors[0]


@asynccontextmanager
async def _startup_lifespan(
    app: FastAPI, cleanups: dict[str, Callable[[], Awaitable[None]]]
) -> AsyncIterator[None]:
    """
    Lifespan context manager for FastAPI application.
    Handles startup and shutdown events.
    """
    # Start Splunk handler async worker (if configured)
    if LoggingConfig.splunk_handler:
        cleanups["splunk"] = LoggingConfig.splunk_handler.stop
        await LoggingConfig.splunk_handler.start()
        logger.info("Splunk HEC handler started.")

    cleanups["executors"] = cpu_executor_registry.aclose_all
    logger.info(
        "Registered CPU executors: %s",
        cpu_executor_registry.count(),
    )

    await cpu_executor_registry.warmup_all()

    # Get Redis instance from dependency injection container
    container = app.container
    agentverse = getattr(container, "agentverse", None)
    genesis = getattr(agentverse, "agent_lifecycle_service", None)
    if isinstance(genesis, providers.Provider):

        async def shutdown_genesis() -> None:
            await genesis().shutdown()

        cleanups["genesis"] = shutdown_genesis
    stack = AsyncExitStack()
    cleanups["redis"] = stack.aclose
    redis_client = await stack.enter_async_context(container.redis.redis_client())

    container.redis.redis_client.override(providers.Object(redis_client))

    redis_repo = container.redis.redis_repository()

    start_sessions = getattr(app.state, "start_genesis_sessions", None)
    if start_sessions is not None:
        cleanups["genesis_sessions"] = app.state.close_genesis_sessions
        await start_sessions()

    rabbitmq = app.container.messaging.rabbitmq_client()
    cleanups["rabbitmq"] = rabbitmq.stop

    await rabbitmq.connect()

    runtime = container.cognitive_event.event_runtime()
    cleanups["runtime"] = runtime.stop

    container.events.redis_repository.override(providers.Object(redis_repo))

    container.operations.event_publisher.override(container.events.domain_event_publisher)

    queue_service = container.redis.queue_service()

    job_supervisor = container.operations.job_supervisor()
    cleanups["supervisor"] = job_supervisor.close

    app.state.job_supervisor = job_supervisor

    await job_supervisor.start()

    await FastAPILimiter.init(redis_client)
    logger.info("FastAPILimiter initialized with Redis")

    # Initialize MongoDB connection
    mongo_client = container.database.mongo_client()
    cleanups["mongo"] = mongo_client.disconnect

    try:
        # Test Redis connection
        await redis_client.ping()
        logger.info("Rate limiter initialized with Redis")

        # Ensure MongoDB connection is established
        await mongo_client.connect()
        logger.info("MongoDB connection established successfully")

        await runtime.start()
        logger.info("Cognitive event runtime started successfully")

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
            "messaging": {"rabbitmq": container.messaging.rabbitmq_service()},
            "knowledge_db": knowledge_db,
        }

        inject_noops(app.state.cognitive_modules)
        cm = app.state.cognitive_modules
        app.state.cognitive_defaults = compute_defaults(cm)

        llm_slug = app.state.cognitive_defaults["llm"]
        default_repo = app.state.cognitive_modules["llm"].get(llm_slug)
        app.state.client_factory = build_client_factory(cm, app.state.cognitive_defaults)

        # Domain checks over the model catalog (e.g. reviewed model allow-lists); any
        # failure aborts startup before domain bootstrap runs.
        for check in getattr(app.state, "model_catalog_checks", ()):
            check(app.state.client_factory)

        # Domain bootstrap that needs the model catalog; any failure aborts startup.
        bootstrap = getattr(app.state, "bootstrap_genesis", None)
        if bootstrap is not None:
            await bootstrap()

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
        except Exception:
            logger.exception("[MCP] Failed to load MCP Proxy tools")

        yield

    except Exception as e:
        logger.error("Error during startup: %s", e)
        raise
