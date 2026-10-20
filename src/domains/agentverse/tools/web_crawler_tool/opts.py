from __future__ import annotations

from src.domains.agentverse.tools.web_crawler_tool.config import (
    WebCrawlerToolConfig,
    WebCrawlerToolParams,
)


def _normalize_config(
    config: WebCrawlerToolConfig | dict | None = None,
) -> WebCrawlerToolConfig:
    if isinstance(config, WebCrawlerToolConfig):
        normalized = config
    elif config:
        normalized = WebCrawlerToolConfig(**config)
    else:
        normalized = WebCrawlerToolConfig()

    return normalized

def _normalize_params_config(
    config:  WebCrawlerToolParams | dict | None = None,
) -> WebCrawlerToolParams:
    if isinstance(config, WebCrawlerToolParams):
        return config

    if isinstance(config, dict):
        return WebCrawlerToolParams(**config)

    return WebCrawlerToolParams()