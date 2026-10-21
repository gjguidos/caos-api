from __future__ import annotations

import logging
from collections import defaultdict

from src.base.config.ai_models_config import AiModelsConfig
from src.base.infrastructure.ai.openai_client import OpenAIClient
from src.base.repositories.openai_repository import OpenAIRepository

logger = logging.getLogger(__name__)


def build_openai_repositories(
    cfg: AiModelsConfig,
) -> dict[str, dict[str, OpenAIRepository]]:
    buckets: dict[str, dict[str, OpenAIRepository]] = defaultdict(dict)

    if not cfg.is_openai_available:
        logger.info("OpenAI not configured - skipping.")
        return dict(buckets)

    logger.info("OpenAI credentials detected. Building repositories...")

    def build_repo(
        chat_model: str | None = None,
        embedding_model: str | None = None,
        whispering_model: str | None = None,
        image_model: str | None = None,
    ) -> OpenAIRepository:
        return OpenAIRepository(
            OpenAIClient(
                api_key=cfg.openai_api_key,
                base_url=cfg.openai_url,
                chat_model=chat_model,
                embedding_model=embedding_model,
                whispering_model=whispering_model,
                image_model=image_model,
            )
        )

    for model in cfg.parsed_openai_chat_models:
        buckets["llm"][f"openai-{model}"] = build_repo(
            chat_model=model,
        )

    for model in cfg.parsed_openai_embedding_models:
        buckets["embedding"][f"openai-{model}"] = build_repo(
            embedding_model=model,
        )

    for model in cfg.parsed_openai_image_models:
        buckets["image"][f"openai-{model}"] = build_repo(
            image_model=model,
        )

    for model in cfg.parsed_openai_transcription_models:
        buckets["audio"][f"openai-{model}"] = build_repo(
            whispering_model=model,
        )

    return dict(buckets)
