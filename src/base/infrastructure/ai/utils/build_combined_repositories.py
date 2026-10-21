from __future__ import annotations

from collections import defaultdict

from src.base.config.ai_models_config import AiModelsConfig
from src.base.infrastructure.ai.utils.build_azure_llm_repositories import (
    build_azure_openai_repositories,
)
from src.base.infrastructure.ai.utils.build_openai_llm_repositories import (
    build_openai_repositories,
)
from src.base.repositories.openai_repository import OpenAIRepository


def build_combined_repositories(
    cfg: AiModelsConfig,
) -> dict[str, dict[str, OpenAIRepository]]:
    combined: dict[
        str, dict[str, OpenAIRepository]
    ] = defaultdict(dict)

    for builder in (
        build_azure_openai_repositories,
        build_openai_repositories,
    ):
        provider_repos = builder(cfg)

        for domain, repos in provider_repos.items():
            for slug, repo in repos.items():
                if slug in combined[domain]:
                    raise ValueError(
                        f"Duplicate repository slug: {domain}/{slug}"
                    )

                combined[domain][slug] = repo

    return dict(combined)
