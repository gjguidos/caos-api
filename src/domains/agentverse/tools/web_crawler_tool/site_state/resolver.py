from __future__ import annotations

from urllib.parse import urlparse

from src.domains.agentverse.tools.web_crawler_tool.site_state.coverage import (
    resolve_site_coverage,
)
from src.domains.agentverse.tools.web_crawler_tool.site_state.models import (
    CoverageRequest,
    CoverageResult,
)
from src.domains.agentverse.tools.web_crawler_tool.site_state.store import (
    SiteStateStore,
)


async def resolve_requested_site_coverage(
    *,
    store: SiteStateStore | None,
    start_url: str,
    max_depth: int,
    freshness_ttl_seconds: int,
) -> CoverageResult | None:
    if store is None:
        return None

    domain = (
        urlparse(start_url).hostname
        or ""
    ).lower()

    if not domain:
        return None

    state = await store.get(domain)

    if state is None:
        return None

    return resolve_site_coverage(
        state=state,
        request=CoverageRequest(
            root_url=start_url,
            max_depth=max_depth,
            freshness_ttl_seconds=freshness_ttl_seconds,
        ),
    )