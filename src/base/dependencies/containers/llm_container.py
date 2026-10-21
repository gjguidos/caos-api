
import logging

from dependency_injector import containers, providers

from src.base.config.ai_models_config import AiModelsConfig
from src.base.config.config import settings as app_settings
from src.base.infrastructure.ai.utils.build_combined_repositories import (
    build_combined_repositories,
)

logger = logging.getLogger(__name__)


def select_repository(
    repositories: dict,
    domain: str,
    slug: str,
):
    group = repositories.get(domain, {})
    repo = group.get(slug)

    if repo is None:
        raise RuntimeError(
            f"No {domain} repository for '{slug}'. "
            f"Available: {list(group.keys())}"
        )

    return repo


def build_default_slug(
    settings: AiModelsConfig,
    domain: str,
) -> str:
    provider = settings.resolved_default_llm

    if domain == "llm":
        if provider == "openai":
            return f"openai-{settings.default_chat_model}"

        deployments = settings.parsed_azure_chat_deployments
        if not deployments:
            raise RuntimeError(
                "No Azure chat deployment configured"
            )
        return f"azure-{deployments[0]}"

    if domain == "embedding":
        if provider == "openai":
            return f"openai-{settings.default_embedding_model}"

        deployments = settings.parsed_azure_embedding_deployments
        if not deployments:
            raise RuntimeError(
                "No Azure embedding deployment configured"
            )
        return f"azure-{deployments[0]}"

    raise ValueError(
        f"Unsupported cognitive domain: {domain}"
    )


class LLMContainer(containers.DeclarativeContainer):
    """Shared LLM repository catalog."""

    settings = providers.Object(
        app_settings.ai_models
    )

    repositories = providers.Singleton(
        build_combined_repositories,
        cfg=settings,
    )

    default_repository = providers.Callable(
        select_repository,
        repositories=repositories,
        domain="llm",
        slug=providers.Callable(
            build_default_slug,
            settings=settings,
            domain="llm",
        ),
    )

    embedding_for = providers.Callable(
        select_repository,
        repositories=repositories,
        domain="embedding",
        slug=providers.Callable(
            build_default_slug,
            settings=settings,
            domain="embedding",
        ),
    )
