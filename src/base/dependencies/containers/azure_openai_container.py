import logging

from dependency_injector import containers, providers

from src.base.config.ai_models_config import AiModelsConfig
from src.base.infrastructure.ai.utils.build_azure_llm_repositories import (
    build_azure_openai_repositories,
)

logger = logging.getLogger(__name__)
settings = AiModelsConfig()

repos_map = build_azure_openai_repositories(settings)


class AzureOpenAIContainer(containers.DeclarativeContainer):
    repositories = providers.Object(repos_map)

    azure_openai_repository = providers.Callable(
        lambda repo_dict: repo_dict["llm"]["azure"],
        repositories,
    )
