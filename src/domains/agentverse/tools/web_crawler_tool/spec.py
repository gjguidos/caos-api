from __future__ import annotations

from src.domains.agentverse.tools.spec import ToolSpec
from src.domains.agentverse.tools.web_crawler_tool.config import WebCrawlerToolConfig
from src.domains.agentverse.tools.web_crawler_tool.params import parameters
from src.domains.agentverse.tools.web_crawler_tool.ui_metadata import (
    WEB_CRAWLER_UI_METADATA,
)

web_crawler_tool = ToolSpec(
    key="web_crawler",
    name="Web Crawler Tool",
    description=(
        "LLM-free enterprise web crawler: fetches web pages, extracts readable text, "
        "collects links, and supports safe bounded same-domain crawling."
    ),
    version="0.1.0",
    tool_class="web_research",
    auto_policy={"enabled": False},
    priority=110,
    tags=["web", "crawler", "scraping", "research", "ingestion"],
    config=WebCrawlerToolConfig().model_dump(),
    runtime_dependencies=[
        {
            "key": "web_crawler.acquisition_context_factory",
            "bind_as": "acquisition_context_factory",
        }
    ],
    parameters=parameters,
    metadata={
        "ownership": "global",
        "provider": "tools:web",
        "llm_free": True,
        "execution_mode": "pure",
        "route": "pure",
        "config_schema": (
            "src.domains.agentverse.tools.web_crawler_tool.WebCrawlerToolConfig"
        ),
        "default_config": WebCrawlerToolConfig().model_dump(),
        "tags": ["web", "crawler", "scraping", "research", "ingestion"],
        "requires": {
            "network": True,
            "tool_runner": False,
        },
        "instructions": (
            "Use this tool to crawl public web pages within bounded enterprise limits. "
            "The tool is LLM-free and returns structured crawl data."
        ),
        "result_contract": {
            "primary_field": "result.answer",
            "secondary_fields": [
                "result.pages",
                "result.components",
                "result.addons.crawler_meta",
                "result.addons.errors",
            ],
            "workflow_handoff_fields": {
                "llm_readable": "result.answer",
                "raw_pages": "result.pages",
                "structured_components": "result.components",
                "metadata": "result.addons.crawler_meta",
                "errors": "result.addons.errors",
            },
        },

        "renderers": {
            "result.answer": "markdown",
            "result.pages": "json",
            "result.components": "json",
            "result.addons.crawler_meta": "json",
            "result.addons.errors": "json",
        },
        "ui": WEB_CRAWLER_UI_METADATA,
        "supports": {
            "streaming": False,
            "attachments": [],
            "multi_turn_memory": False,
        },
    },
)