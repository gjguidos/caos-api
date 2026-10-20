from __future__ import annotations

from typing import Any

from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.tools.web_crawler_tool.site_state.store import (
    SiteStateStore,
)


def build_site_state_store(
    *,
    repo: Any | None,
    user_id: str | None,
    ttl_seconds: int = 86_400,
) -> SiteStateStore | None:
    if repo is None:
        return None
    AGENTVERSE_LOGGER.info(
        LogDomain.TOOL_BAY,
        "[🕷️ WEB CRAWLER][BUILD SITE STATE STORE]",
    )

    return SiteStateStore(
        repo=repo,
        user_id=user_id,
        ttl_seconds=ttl_seconds,
    )