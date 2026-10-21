from __future__ import annotations

import logging
from collections import defaultdict

from src.base.config.ai_models_config import AiModelsConfig
from src.base.infrastructure.ai.azure_openai_client import AzureOpenAIClient
from src.base.repositories.openai_repository import OpenAIRepository

logger = logging.getLogger(__name__)


def build_azure_openai_repositories(cfg: AiModelsConfig) -> dict[str, dict[str, OpenAIRepository]]:
    buckets: dict[str, dict[str, OpenAIRepository]] = defaultdict(dict)

    if not cfg.is_azure_configured:
        logger.warning("Azure not configured - skipping.")
        return buckets

    logger.info("✅ Azure credentials detected. Building Azure repositories...")

    def build_repo(
        chat_deployment: str | None = None,
        completion_deployment: str | None = None,
        embedding_deployment: str | None = None,
        whispering_deployment: str | None = None,
        image_deployment: str | None = None,
    ) -> OpenAIRepository:
        return OpenAIRepository(
            AzureOpenAIClient(
                endpoint=cfg.azure_openai_endpoint,
                api_key=cfg.azure_openai_api_key,
                chat_deployment=chat_deployment,
                completion_deployment=completion_deployment,
                embedding_deployment=embedding_deployment,
                whispering_deployment=whispering_deployment,
                image_deployment=image_deployment,
            )
        )

    # LLMs
    for deployment in cfg.parsed_azure_chat_deployments:
        slug = f"azure-{deployment}"
        buckets["llm"][slug] = build_repo(chat_deployment=deployment)

    # Embedding
    for deployment in cfg.parsed_azure_embedding_deployments:
        slug = f"azure-{deployment}"
        buckets["embedding"][slug] = build_repo(embedding_deployment=deployment)

    # Whisper / Audio
    for deployment in cfg.parsed_azure_whispering_deployments:
        slug = f"azure-{deployment}"
        buckets["audio"][slug] = build_repo(whispering_deployment=deployment)

    # Image
    for deployment in cfg.parsed_azure_image_deployments:
        slug = f"azure-{deployment}"
        buckets["image"][slug] = build_repo(image_deployment=deployment)

    # Optional: reuse image for video
    for deployment in cfg.parsed_azure_image_deployments:
        buckets["video"][f"azure-video-{deployment}"] = buckets["image"][f"azure-{deployment}"]

    return buckets
