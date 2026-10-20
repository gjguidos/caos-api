from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import Any
from urllib.parse import urlparse

from src.domains.agentverse.agents.agent_interface import (
    AgentInterface,
)
from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.tools.web_crawler_tool.auth.context import (
    CrawlAuthContext,
)
from src.domains.agentverse.tools.web_crawler_tool.config import (
    WebCrawlerSecurityConfig,
)
from src.domains.agentverse.tools.web_crawler_tool.engine.crawler import (
    CrawlEngine,
    CrawlProgressCallback,
    CrawlResult,
)
from src.domains.agentverse.tools.web_crawler_tool.runtime import (
    ResolvedCrawlerRuntime,
)
from src.domains.agentverse.tools.web_crawler_tool.snapshots.fingerprint import (
    resolve_acquisition_fingerprint,
)
from src.domains.agentverse.tools.web_crawler_tool.snapshots.models import (
    CrawlSnapshot,
)
from src.domains.agentverse.tools.web_crawler_tool.snapshots.store import (
    CrawlSnapshotStore,
)


@dataclass(frozen=True)
class ResolvedCrawlEvidence:
    result: CrawlResult

    source: str
    reused: bool

    snapshot_id: str | None
    snapshot_created_at: datetime | None

    acquisition_fingerprint: str

    @property
    def snapshot_age_seconds(
        self,
    ) -> float | None:
        if self.snapshot_created_at is None:
            return None

        return max(
            0.0,
            (
                datetime.now(UTC)
                - self.snapshot_created_at
            ).total_seconds(),
        )


async def resolve_crawl_evidence(
    *,
    runtime: ResolvedCrawlerRuntime,
    start_url: str,
    crawl_mode: str,
    fetcher: Any,
    auth_context: CrawlAuthContext | None,
    agent: AgentInterface | None,
    snapshot_store: CrawlSnapshotStore | None,
    rate_limit_delay_ms: int,
    security: WebCrawlerSecurityConfig,
    on_progress: CrawlProgressCallback | None = None,
    target_urls: list[str] | None = None,
    acquisition_limit: int | None = None,
    acquisition_concurrency: int | None = None,
) -> ResolvedCrawlEvidence:
    """
    Resolve crawl evidence from either a compatible session snapshot
    or a fresh CrawlEngine execution.

    Projection-only options such as find, filters and report selection
    do not affect acquisition identity.

    Progress reporting is optional and transport-agnostic. When a fresh
    crawl is required, the callback is forwarded to CrawlEngine.
    """

    acquisition, fingerprint = resolve_acquisition_fingerprint(
        runtime=runtime,
        start_url=start_url,
        crawl_mode=crawl_mode,
        target_urls=target_urls,
        acquisition_limit=acquisition_limit,
    )

    # --------------------------------------------------------------
    # Snapshot reuse
    # --------------------------------------------------------------

    if snapshot_store is not None:
        snapshot = await snapshot_store.get(
            fingerprint
        )

        if snapshot is not None:
            return ResolvedCrawlEvidence(
                result=snapshot.to_crawl_result(),
                source="snapshot",
                reused=True,
                snapshot_id=snapshot.snapshot_id,
                snapshot_created_at=snapshot.created_at,
                acquisition_fingerprint=fingerprint,
            )

    # --------------------------------------------------------------
    # Fresh acquisition
    # --------------------------------------------------------------

    crawl_started = perf_counter()

    crawl_result = await CrawlEngine().crawl(
        agent=agent,
        start_url=start_url,
        mode=crawl_mode,
        fetcher=fetcher,
        auth_context=auth_context,
        target_urls=(
            acquisition["target_urls"]
            if target_urls is not None
            else None
        ),
        acquisition_concurrency=acquisition_concurrency,
        max_pages=acquisition["max_pages"],
        max_depth=acquisition["max_depth"],
        crawl_strategy=runtime.crawl_strategy,
        same_domain_only=runtime.same_domain_only,
        include_links=runtime.include_links,
        rate_limit_delay_ms=rate_limit_delay_ms,
        security=security,
        context=runtime.params.context,
        on_progress=on_progress,
    )

    crawl_seconds = perf_counter() - crawl_started
    
    AGENTVERSE_LOGGER.debug(
    LogDomain.TOOL_BAY,
    (
        f"[WEB CRAWLER][EVIDENCE][REPORT TRACE] "
        f"pages={len(crawl_result.pages)} "
        f"first_reports={sorted((crawl_result.pages[0].reports or {}).keys()) if crawl_result.pages else []} "
        f"first_media={((crawl_result.pages[0].reports or {}).get('media') or {}).get('total_media', 0) if crawl_result.pages else 0}"
    ),
)
    # --------------------------------------------------------------
    # Persist snapshot
    # --------------------------------------------------------------

    snapshot_seconds = 0.0
    snapshot_id: str | None = None
    snapshot_created_at: datetime | None = None

    if snapshot_store is not None:
        snapshot_started = perf_counter()
        snapshot = CrawlSnapshot.from_crawl_result(
            result=crawl_result,
            session_id=snapshot_store.session_id,
            user_id=snapshot_store.user_id,
            domain=_domain_from_url(start_url),
            acquisition_fingerprint=fingerprint,
            acquisition=acquisition,
        )

        await snapshot_store.put(
            snapshot
        )

        snapshot_seconds = (
            perf_counter() - snapshot_started
        )
        snapshot_id = snapshot.snapshot_id
        snapshot_created_at = snapshot.created_at

        AGENTVERSE_LOGGER.debug(
            LogDomain.TOOL_BAY,
            (
                "[WEB CRAWLER][EVIDENCE][TIMING] "
                f"pages={len(crawl_result.pages)} "
                f"crawl_seconds={crawl_seconds:.3f} "
                f"snapshot_seconds={snapshot_seconds:.3f}"
            ),
        )

    return ResolvedCrawlEvidence(
        result=crawl_result,
        source="network",
        reused=False,
        snapshot_id=snapshot_id,
        snapshot_created_at=snapshot_created_at,
        acquisition_fingerprint=fingerprint,
    )


def _domain_from_url(
    url: str,
) -> str:
    return (
        urlparse(url).hostname
        or ""
    ).lower()