from __future__ import annotations

from dataclasses import dataclass

from src.domains.agentverse.agents.agent_interface import (
    AgentInterface,
)
from src.domains.agentverse.tools.base import ToolExecutionError
from src.domains.agentverse.tools.web_crawler_tool.auth.context import (
    CrawlAuthContext,
)
from src.domains.agentverse.tools.web_crawler_tool.auth.resolver import (
    CrawlAuthContextResolver,
)
from src.domains.agentverse.tools.web_crawler_tool.config import (
    WebCrawlerToolConfig,
)
from src.domains.agentverse.tools.web_crawler_tool.helpers.resources import domain_from_url
from src.domains.agentverse.tools.web_crawler_tool.helpers.urls import (
    normalize_url,
)
from src.domains.agentverse.tools.web_crawler_tool.runtime import (
    ResolvedCrawlerRuntime,
)
from src.domains.agentverse.tools.web_crawler_tool.security.policy import (
    CrawlerSecurityError,
    enforce_url_security,
)


@dataclass(frozen=True)
class PreparedCrawlExecution:
    domain: str
    start_url: str
    crawl_mode: str
    auth_context: CrawlAuthContext | None


async def prepare_crawl_execution(
    *,
    cfg: WebCrawlerToolConfig,
    runtime: ResolvedCrawlerRuntime,
    agent: AgentInterface | None,
) -> PreparedCrawlExecution:
    try:
        start_url = normalize_url(
            runtime.params.url
        )

        enforce_url_security(
            start_url,
            security=cfg.security,
            start_url=start_url,
        )

    except CrawlerSecurityError as exc:
        raise ToolExecutionError(
            str(exc)
        ) from exc

    auth_context = await CrawlAuthContextResolver().resolve(
        auth_mode=runtime.auth_mode,
        browser_session_id=runtime.params.browser_session_id,
        target_url=start_url,
        context=runtime.params.context,
        agent=agent,
    )

    domain = domain_from_url(start_url)
    crawl_mode = runtime.crawl_mode

    if (
        auth_context
        and auth_context.mode == "browser_context"
        and crawl_mode == "static"
    ):
        crawl_mode = "browser"

    if (
        crawl_mode == "browser"
        and not cfg.allow_browser_mode
    ):
        raise ToolExecutionError(
            "Browser crawling is disabled "
            "for this tool configuration."
        )

    return PreparedCrawlExecution(
        domain=domain,
        start_url=start_url,
        crawl_mode=crawl_mode,
        auth_context=auth_context,
    )