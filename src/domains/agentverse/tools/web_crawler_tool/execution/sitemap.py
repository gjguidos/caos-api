from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import perf_counter
from typing import Any
from urllib.parse import urlparse

from src.domains.agentverse.agents.agent_interface import AgentInterface
from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.tools.web_crawler_tool.config import (
    WebCrawlerToolConfig,
)
from src.domains.agentverse.tools.web_crawler_tool.helpers.prepare_crawl_execution import (
    PreparedCrawlExecution,
)
from src.domains.agentverse.tools.web_crawler_tool.helpers.urls import (
    same_domain,
)
from src.domains.agentverse.tools.web_crawler_tool.planning.coverage_builder import (
    build_crawl_plan_from_sitemap_planning_coverage,
)
from src.domains.agentverse.tools.web_crawler_tool.planning.models import (
    CrawlPlan,
)
from src.domains.agentverse.tools.web_crawler_tool.runtime import (
    ResolvedCrawlerRuntime,
)
from src.domains.agentverse.tools.web_crawler_tool.site_state.factory import (
    build_site_state_store,
)
from src.domains.agentverse.tools.web_crawler_tool.sitemap.catalog import (
    build_sitemap_catalog,
)
from src.domains.agentverse.tools.web_crawler_tool.sitemap.coverage import (
    resolve_sitemap_planning_coverage,
)
from src.domains.agentverse.tools.web_crawler_tool.sitemap.discovery import (
    discover_sitemap_urls,
)
from src.domains.agentverse.tools.web_crawler_tool.sitemap.evidence import (
    resolve_sitemap_evidence,
)
from src.domains.agentverse.tools.web_crawler_tool.sitemap.factory import (
    build_sitemap_state_store,
)
from src.domains.agentverse.tools.web_crawler_tool.sitemap.fetcher import (
    SitemapFetcher,
)
from src.domains.agentverse.tools.web_crawler_tool.sitemap.models import (
    SitemapDiscoveryResult,
    SitemapPlanningCoverage,
)
from src.domains.agentverse.tools.web_crawler_tool.sitemap.progress import (
    SitemapProgressCallback,
)
from src.domains.agentverse.tools.web_crawler_tool.sitemap.resolver import (
    SitemapResolver,
)
from src.domains.agentverse.tools.web_crawler_tool.sitemap.selection import (
    resolve_sitemap_selection,
)
from src.domains.agentverse.tools.web_crawler_tool.sitemap.types import (
    SitemapDiscoveryMode,
    SitemapParseProfile,
    SitemapSelectionScope,
    SitemapTraversalPolicy,
)
from src.domains.agentverse.tools.web_crawler_tool.sitemap.url_inventory_store import (
    SitemapUrlInventoryStore,
)


@dataclass(frozen=True, slots=True)
class SitemapScopePlan:
    """
    Internal result of sitemap discovery and crawl planning.

    This is intentionally not the final tool payload. It preserves the
    typed discovery, coverage, and crawl-plan objects so the operation
    executor can later continue into planned page acquisition.
    """

    discovery: SitemapDiscoveryResult | None
    coverage: SitemapPlanningCoverage | None
    crawl_plan: CrawlPlan | None

    # Compact JSON-safe sitemap result used for current delivery.
    planning_result: dict[str, Any]

    # Human-readable summary of the planning operation.
    answer: str

    def to_payload(self) -> dict[str, Any]:
        """
        Preserve the existing planning-only tool response.

        This adapter can be removed or moved to the delivery layer once
        sitemap plans continue into page acquisition.
        """
        return {
            "answer": self.answer,
            "message": self.answer,
            "text": self.answer,
            "display_text": self.answer,
            "result": self.planning_result,
            "artifacts": [],
            "error": None,
        }

async def execute_sitemap_scope(
    *,
    run_id: str,
    cfg: WebCrawlerToolConfig,
    runtime: ResolvedCrawlerRuntime,
    prepared: PreparedCrawlExecution,
    agent: AgentInterface | None,
    sitemap_url_inventory_store: SitemapUrlInventoryStore | None,
    on_progress: SitemapProgressCallback | None = None,
) -> SitemapScopePlan:
    """Run sitemap discovery with a pooled HTTP client and close it safely."""
    fetcher = SitemapFetcher(
        timeout_seconds=runtime.timeout_seconds,
        max_response_bytes=cfg.sitemap_max_response_bytes,
    )

    try:
        return await _execute_sitemap_scope(
            run_id=run_id,
            cfg=cfg,
            runtime=runtime,
            prepared=prepared,
            agent=agent,
            sitemap_url_inventory_store=sitemap_url_inventory_store,
            on_progress=on_progress,
            sitemap_fetcher=fetcher,
        )
    finally:
        await fetcher.aclose()


async def _execute_sitemap_scope(
    *,
    run_id: str,
    cfg: WebCrawlerToolConfig,
    runtime: ResolvedCrawlerRuntime,
    prepared: PreparedCrawlExecution,
    agent: AgentInterface | None,
    sitemap_url_inventory_store: SitemapUrlInventoryStore | None,
    on_progress: SitemapProgressCallback | None,
    sitemap_fetcher: SitemapFetcher,
) -> SitemapScopePlan:
    """
    Execute sitemap-scope discovery and crawl planning.

    Responsibilities:
      - resolve root sitemap evidence
      - reuse cached sitemap topology when fresh
      - build a structural catalog for sitemap indexes
      - resolve an optional sitemap selection
      - recursively discover page <loc> URLs
      - compare discovered page URLs with SiteState
      - build a bounded crawl plan

    Sitemap index behavior:
      - selection provided:
          recursively discover only selected sitemap branches
      - no selection:
          recursively discover the complete sitemap tree

    Important:
      - discovery.page_urls is the authoritative sitemap acquisition input
      - sitemap-declared media/resources are not authoritative page evidence
      - actual media/document reporting will come from crawling the
        discovered page URLs through the normal page acquisition engine

    Does not yet:
      - execute the crawl plan
      - acquire planned page targets
      - update SiteState from planned page acquisitions
    """

    AGENTVERSE_LOGGER.info(
        LogDomain.TOOL_BAY,
        "[🕷️ WEB CRAWLER][SITE_MAP][EXECUTOR_V3] Start Resolving",
    )

    # ------------------------------------------------------------------
    # Sitemap acquisition infrastructure.
    # ------------------------------------------------------------------
    sitemap_cpu_semaphore = asyncio.Semaphore(
        4
    )
    
    resolver = SitemapResolver(
        fetcher=sitemap_fetcher,
        cpu_semaphore=sitemap_cpu_semaphore,
    )

    sitemap_state_store = build_sitemap_state_store(
        repo=getattr(
            agent,
            "cache",
            None,
        ),
        session_id=getattr(
            agent,
            "default_session_id",
            None,
        ),
        user_id=getattr(
            agent,
            "user_id",
            None,
        ),
        ttl_seconds=cfg.sitemap_state_ttl_seconds,
    )

    # ------------------------------------------------------------------
    # Root sitemap evidence.
    # ------------------------------------------------------------------
    root_evidence = await resolve_sitemap_evidence(
        url=prepared.start_url,
        resolver=resolver,
        store=sitemap_state_store,
        freshness_ttl_seconds=(
            cfg.sitemap_freshness_ttl_seconds
        ),
    )

    topology = root_evidence.topology

    AGENTVERSE_LOGGER.info(
        LogDomain.TOOL_BAY,
        (
            "[🕷️ WEB CRAWLER]"
            "[SITE_MAP]"
            "[ROOT_EVIDENCE] "
            f"source={root_evidence.source} "
            f"reused={root_evidence.reused} "
            f"type={topology.source_type}"
        ),
    )

    # ------------------------------------------------------------------
    # Execution state.
    # ------------------------------------------------------------------
    catalog = None
    selection = runtime.params.sitemap_selection

    selected_partition_urls: list[str] = []

    requested_mode: SitemapDiscoveryMode = (
        runtime.sitemap_discovery_mode
    )

    traversal_policy: SitemapTraversalPolicy = (
        runtime.sitemap_traversal_policy
    )

    max_sitemaps = runtime.max_sitemaps

    planning_window_size = (
        runtime.page_acquisition_limit
        if runtime.page_acquisition_limit is not None
        else cfg.crawl_plan_chunk_size
    )

    discovery_mode: SitemapDiscoveryMode = (
        requested_mode
    )

    parse_profile: SitemapParseProfile = (
        "locations"
        if requested_mode == "planned"
        else "rich"
    )

    selection_scope: SitemapSelectionScope = (
        "selected"
        if selection is not None
        else "all"
    )

    discovery = None

    coverage = None
    crawl_plan = None

    AGENTVERSE_LOGGER.info(
        LogDomain.TOOL_BAY,
        (
            "[🕷️ WEB CRAWLER]"
            "[SITE_MAP]"
            "[DISCOVERY_REQUEST] "
            f"mode={discovery_mode} "
            f"parse_profile={parse_profile} "
            f"selection_scope={selection_scope} "
            f"traversal_policy={traversal_policy} "
            f"max_sitemaps={max_sitemaps}"
        ),
    )

    # ------------------------------------------------------------------
    # Sitemap index.
    # ------------------------------------------------------------------
    if topology.source_type == "sitemap_index":
        catalog = build_sitemap_catalog(
            topology=topology
        )

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            (
                "[🕷️ WEB CRAWLER]"
                "[SITE_MAP]"
                "[CATALOG] "
                f"partitions={catalog.partition_count} "
                f"root_groups={len(catalog.roots)}"
            ),
        )

        # --------------------------------------------------------------
        # Selected sitemap discovery.
        # --------------------------------------------------------------
        if selection is not None:
            selected_partition_urls = (
                resolve_sitemap_selection(
                    catalog=catalog,
                    selection=selection,
                )
            )

            AGENTVERSE_LOGGER.info(
                LogDomain.TOOL_BAY,
                (
                    "[🕷️ WEB CRAWLER]"
                    "[SITE_MAP]"
                    "[SELECTION_RESOLVED] "
                    f"partitions="
                    f"{len(selected_partition_urls)}"
                ),
            )

            if selected_partition_urls:
                discovery = await discover_sitemap_urls(
                    run_id=run_id,
                    root_url=prepared.start_url,

                    start_urls=selected_partition_urls,

                    # Explicitly selected sitemap resources establish the
                    # traversal boundary. They are traversal anchors and
                    # therefore do not consume max_sitemaps themselves.
                    # Descendant sitemap resources discovered beneath them do.
                    start_urls_count_as_descendants=False,

                    pre_resolved_urls=[
                        prepared.start_url
                    ],

                    traversal_policy=traversal_policy,
                    max_sitemaps=max_sitemaps,

                    resolver=resolver,
                    store=sitemap_state_store,
                    url_inventory_store=sitemap_url_inventory_store,
                    page_url_return_limit=planning_window_size,
                    max_concurrency=(
                        cfg.sitemap_discovery_concurrency
                    ),
                    freshness_ttl_seconds=(
                        cfg.sitemap_freshness_ttl_seconds
                    ),
                    cpu_semaphore=sitemap_cpu_semaphore,
                    on_progress=on_progress,
                )

            else:
                AGENTVERSE_LOGGER.warning(
                    LogDomain.TOOL_BAY,
                    (
                        "[🕷️ WEB CRAWLER]"
                        "[SITE_MAP]"
                        "[SELECTION_EMPTY] "
                        "selection resolved to zero sitemap resources"
                    ),
                )

        # --------------------------------------------------------------
        # Full sitemap discovery.
        #
        # No selection means recursively walk the complete sitemap tree.
        # --------------------------------------------------------------
        else:

            root_partition_urls = [
                partition.url
                for partition in topology.partitions
            ]

            AGENTVERSE_LOGGER.info(
                LogDomain.TOOL_BAY,
                (
                    "[🕷️ WEB CRAWLER]"
                    "[SITE_MAP]"
                    "[DISCOVERY_TREE_REQUESTED] "
                    f"mode={discovery_mode} "
                    f"parse_profile={parse_profile} "
                    f"selection_scope={selection_scope} "
                    f"root={prepared.start_url} "
                    f"partitions={len(root_partition_urls)}"
                ),
            )

            discovery = await discover_sitemap_urls(
                run_id=run_id,
                root_url=prepared.start_url,

                start_urls=root_partition_urls,

                # The root itself is already resolved and is the anchor.
                # These start URLs are its descendants and therefore
                # consume max_sitemaps.
                start_urls_count_as_descendants=True,

                pre_resolved_urls=[
                    prepared.start_url
                ],

                traversal_policy=traversal_policy,
                max_sitemaps=max_sitemaps,

                resolver=resolver,
                store=sitemap_state_store,
                url_inventory_store=sitemap_url_inventory_store,
                page_url_return_limit=planning_window_size,
                max_concurrency=(
                    cfg.sitemap_discovery_concurrency
                ),
                freshness_ttl_seconds=(
                    cfg.sitemap_freshness_ttl_seconds
                ),
                cpu_semaphore=sitemap_cpu_semaphore,
                on_progress=on_progress,
            )

    # ------------------------------------------------------------------
    # Plain sitemap/urlset.
    # ------------------------------------------------------------------
    else:
        discovery = await discover_sitemap_urls(
            run_id=run_id,
            root_url=prepared.start_url,

            # A plain urlset is itself the traversal anchor.
            # It must be processed so its page URLs can be collected.
            start_urls_count_as_descendants=False,

            traversal_policy=(
                runtime.sitemap_traversal_policy
            ),
            max_sitemaps=(
                runtime.max_sitemaps
            ),

            resolver=resolver,
            store=sitemap_state_store,
            url_inventory_store=sitemap_url_inventory_store,
            page_url_return_limit=planning_window_size,
            max_concurrency=(
                cfg.sitemap_discovery_concurrency
            ),
            freshness_ttl_seconds=(
                cfg.sitemap_freshness_ttl_seconds
            ),
            cpu_semaphore=sitemap_cpu_semaphore,
            on_progress=on_progress,
        )

    # ------------------------------------------------------------------
    # Discovery summary.
    # ------------------------------------------------------------------
    if discovery is not None:
        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            (
                "[🕷️ WEB CRAWLER]"
                "[SITE_MAP]"
                "[DISCOVERY] "
                f"mode={discovery_mode} "
                f"sitemaps={discovery.sitemap_count} "
                f"pages={discovery.page_url_count} "
                f"failed={discovery.failed_sitemap_count} "
                f"max_depth={discovery.max_depth_reached}"
            ),
        )

    # ------------------------------------------------------------------
    # Discovery inventory.
    #
    # This deliberately represents sitemap discovery rather than rich
    # sitemap-declared resource inventory.
    #
    # Actual media/documents will be determined by fetching the page URLs.
    # ------------------------------------------------------------------
    inventory: dict[str, Any] = {
        "sitemap_count": (
            discovery.sitemap_count
            if discovery is not None
            else 0
        ),
        "page_url_count": (
            discovery.page_url_count
            if discovery is not None
            else 0
        ),
        "failed_sitemap_count": (
            discovery.failed_sitemap_count
            if discovery is not None
            else 0
        ),
        "max_depth_reached": (
            discovery.max_depth_reached
            if discovery is not None
            else 0
        ),
    }

    AGENTVERSE_LOGGER.info(
        LogDomain.TOOL_BAY,
        (
            "[🕷️ WEB CRAWLER]"
            "[SITE_MAP]"
            "[INVENTORY] "
            f"sitemaps={inventory['sitemap_count']} "
            f"pages={inventory['page_url_count']} "
            f"failed={inventory['failed_sitemap_count']} "
            f"max_depth={inventory['max_depth_reached']}"
        ),
    )

    # ------------------------------------------------------------------
    # SiteState.
    # ------------------------------------------------------------------
    site_state_store = build_site_state_store(
        repo=getattr(
            agent,
            "cache",
            None,
        ),
        user_id=getattr(
            agent,
            "user_id",
            None,
        ),
        ttl_seconds=cfg.site_state_ttl_seconds,
    )

    domain = (
        urlparse(
            prepared.start_url
        ).hostname
        or ""
    ).lower()

    # ------------------------------------------------------------------
    # SiteState diagnostics.
    #
    # Temporary instrumentation used to determine whether the event-loop
    # stall originates from:
    #
    #   1. sitemap-discovery finalization / GIL pressure
    #   2. Redis itself
    #   3. the SiteState GET operation specifically
    #
    # Order matters:
    #
    #   yield -> ping -> get
    #
    # This makes the three boundaries independently observable.
    # ------------------------------------------------------------------

    AGENTVERSE_LOGGER.info(
        LogDomain.TOOL_BAY,
        (
            "[🕷️ WEB CRAWLER]"
            "[SITE_MAP]"
            "[EVENT_LOOP_YIELD_START]"
        ),
    )

    yield_started = perf_counter()

    await asyncio.sleep(0)

    yield_seconds = (
        perf_counter()
        - yield_started
    )

    AGENTVERSE_LOGGER.info(
        LogDomain.TOOL_BAY,
        (
            "[🕷️ WEB CRAWLER]"
            "[SITE_MAP]"
            "[EVENT_LOOP_YIELD_COMPLETE] "
            f"seconds={yield_seconds:.3f}"
        ),
    )

    # --------------------------------------------------------------
    # SiteState lookup.
    # --------------------------------------------------------------
    AGENTVERSE_LOGGER.info(
        LogDomain.TOOL_BAY,
        (
            "[🕷️ WEB CRAWLER]"
            "[SITE_MAP]"
            "[SITE_STATE_GET_START]"
        ),
    )

    site_state_started = perf_counter()

    site_state = (
        await site_state_store.get(
            domain
        )
        if (
            site_state_store is not None
            and domain
        )
        else None
    )

    site_state_seconds = (
        perf_counter()
        - site_state_started
    )

    AGENTVERSE_LOGGER.info(
        LogDomain.TOOL_BAY,
        (
            "[🕷️ WEB CRAWLER]"
            "[SITE_MAP]"
            "[SITE_STATE_GET_COMPLETE] "
            f"seconds={site_state_seconds:.3f}"
        ),
    )

    # ------------------------------------------------------------------
    # URL-based sitemap coverage.
    #
    # discovery.page_urls is now authoritative for crawl planning.
    # ------------------------------------------------------------------

    discovered_page_urls = (
        discovery.page_urls
        if discovery is not None
        else []
    )

    eligible_page_urls: list[str] = []
    excluded_by_scope_count = 0

    for url in discovered_page_urls:
        if (
            runtime.same_domain_only
            and not same_domain(url, prepared.start_url)
        ):
            excluded_by_scope_count += 1
            continue

        eligible_page_urls.append(url)

    AGENTVERSE_LOGGER.info(
        LogDomain.TOOL_BAY,
        (
            "[🕷️ WEB CRAWLER][SITE_MAP][SCOPE] "
            f"discovered={discovery.page_url_count if discovery else 0} "
            f"eligible={len(eligible_page_urls)} "
            f"excluded_by_scope={excluded_by_scope_count}"
        ),
    )

    if eligible_page_urls:
        coverage_started = perf_counter()

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            (
                "[🕷️ WEB CRAWLER]"
                "[SITE_MAP]"
                "[COVERAGE_START] "
                f"urls={len(eligible_page_urls)}"
            ),
        )

        

        coverage = await asyncio.to_thread(
            resolve_sitemap_planning_coverage,
            state=site_state,
            urls=eligible_page_urls,
            freshness_ttl_seconds=cfg.page_freshness_ttl_seconds,
            max_candidates=planning_window_size,
        )

        coverage_seconds = (
            perf_counter()
            - coverage_started
        )

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            (
                "[🕷️ WEB CRAWLER]"
                "[SITE_MAP]"
                "[COVERAGE_COMPLETE] "
                f"discovered="
                f"{coverage.discovered_url_count} "
                f"reusable="
                f"{coverage.reusable_url_count} "
                f"stale="
                f"{coverage.stale_url_count} "
                f"missing="
                f"{coverage.missing_url_count} "
                f"stale_candidates="
                f"{len(coverage.stale_candidates)} "
                f"missing_candidates="
                f"{len(coverage.missing_candidates)} "
                f"seconds={coverage_seconds:.3f}"
            ),
        )

        plan_started = perf_counter()

        crawl_plan = await asyncio.to_thread(
            build_crawl_plan_from_sitemap_planning_coverage,
            root_url=prepared.start_url,
            coverage=coverage,
            strategy=runtime.crawl_strategy,
            chunk_size=cfg.crawl_plan_chunk_size,
            max_urls=planning_window_size,
        )

        plan_seconds = (
            perf_counter()
            - plan_started
        )

        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            (
                "[🕷️ WEB CRAWLER]"
                "[SITE_MAP]"
                "[CRAWL_PLAN] "
                f"discovered="
                f"{coverage.discovered_url_count} "
                f"reusable="
                f"{coverage.reusable_url_count} "
                f"stale="
                f"{coverage.stale_url_count} "
                f"missing="
                f"{coverage.missing_url_count} "
                f"selected="
                f"{crawl_plan.selected_count} "
                f"truncated="
                f"{crawl_plan.truncated} "
                f"coverage_seconds="
                f"{coverage_seconds:.3f} "
                f"plan_seconds="
                f"{plan_seconds:.3f}"
            ),
        )

    else:
        reason = (
            "no_urls_match_scope"
            if discovery is not None and discovery.page_url_count > 0
            else "no_discovered_page_urls"
        )
        AGENTVERSE_LOGGER.info(
            LogDomain.TOOL_BAY,
            (
                "[🕷️ WEB CRAWLER]"
                "[SITE_MAP]"
                "[CRAWL_PLAN_SKIPPED] "
                f"reason={reason}"
            ),
        )

    # ------------------------------------------------------------------
    # Result.
    #
    # Keep large page URL inventories out of the top-level tool result.
    # The crawl plan contains only the bounded acquisition selection.
    # Full discovery inventories should eventually become artifacts.
    # ------------------------------------------------------------------
    result: dict[str, Any] = {
        "crawl_scope": "sitemap",
        "discovery_mode": discovery_mode,
        "final_url": root_evidence.final_url,
        "source_type": topology.source_type,
        "partition_count": len(
            topology.partitions
        ),
        "resolved_url_count": (
            discovery.page_url_count
            if discovery is not None
            else 0
        ),
        "inventory": inventory,
        "sitemap_evidence": {
            "source": root_evidence.source,
            "reused": root_evidence.reused,
            "etag": root_evidence.etag,
            "last_modified": (
                root_evidence.last_modified
            ),
        },
    }

    result["scope"] = {
        "root_url": prepared.start_url,
        "same_domain_only": runtime.same_domain_only,
        "discovered_url_count": (
            discovery.page_url_count
            if discovery is not None
            else 0
        ),
        "eligible_url_count": len(eligible_page_urls),
        "excluded_by_scope_count": excluded_by_scope_count,
    }

    # ------------------------------------------------------------------
    # Discovery result.
    #
    # Intentionally do not serialize discovery.page_urls here.
    # On a large sitemap this may contain hundreds of thousands of URLs.
    # ------------------------------------------------------------------
    if discovery is not None:
        result["discovery"] = {
            "root_url": discovery.root_url,
            "traversal_policy": traversal_policy,
            "max_sitemaps": max_sitemaps,
            "page_acquisition_limit": (
                runtime.page_acquisition_limit
            ),
            "planning_window_size": (
                planning_window_size
                if eligible_page_urls
                else None
            ),
            "sitemap_count": (
                discovery.sitemap_count
            ),
            "page_url_count": (
                discovery.page_url_count
            ),
            "failed_sitemap_count": (
                discovery.failed_sitemap_count
            ),
            "max_depth_reached": (
                discovery.max_depth_reached
            ),
            "failed_sitemaps": [
                item.model_dump(
                    mode="json"
                )
                for item
                in discovery.failed_sitemaps
            ],
        }

    # ------------------------------------------------------------------
    # Coverage.
    # ------------------------------------------------------------------
    if coverage is not None:
        discovered_url_count = (
            coverage.discovered_url_count
        )

        reusable_url_count = (
            coverage.reusable_url_count
        )

        stale_url_count = (
            coverage.stale_url_count
        )

        missing_url_count = (
            coverage.missing_url_count
        )

        result["coverage"] = {
            "discovered_url_count": (
                discovered_url_count
            ),
            "reusable_url_count": (
                reusable_url_count
            ),
            "stale_url_count": (
                stale_url_count
            ),
            "missing_url_count": (
                missing_url_count
            ),
            "retained_stale_candidate_count": len(
                coverage.stale_candidates
            ),
            "retained_missing_candidate_count": len(
                coverage.missing_candidates
            ),
        }

        result["discovered_url_count"] = (
            discovered_url_count
        )

        result["reusable_url_count"] = (
            reusable_url_count
        )

        result["stale_url_count"] = (
            stale_url_count
        )

        result["missing_url_count"] = (
            missing_url_count
        )

    # ------------------------------------------------------------------
    # Crawl plan.
    # ------------------------------------------------------------------
    if crawl_plan is not None:
        result["crawl_plan"] = (
            crawl_plan.model_dump(
                mode="json"
            )
        )

    # ------------------------------------------------------------------
    # Sitemap catalog + selection.
    # ------------------------------------------------------------------
    if catalog is not None:
        result["catalog"] = (
            catalog.model_dump(
                mode="json"
            )
        )

        result["catalog_root_count"] = len(
            catalog.roots
        )

        result["selection"] = (
            selection.model_dump(
                mode="json"
            )
            if selection is not None
            else None
        )

        result["selected_partition_count"] = len(
            selected_partition_urls
        )

        result["selected_partition_urls"] = (
            selected_partition_urls
        )

    # ------------------------------------------------------------------
    # Human-readable answer.
    # ------------------------------------------------------------------
    discovered_page_count = (
        discovery.page_url_count
        if discovery is not None
        else 0
    )

    failed_sitemap_count = (
        discovery.failed_sitemap_count
        if discovery is not None
        else 0
    )

    selected_target_count = (
        crawl_plan.selected_count
        if crawl_plan is not None
        else 0
    )

    if topology.source_type == "sitemap_index":
        if selection is not None:
            answer = (
                "Sitemap selection resolved. "
                f"Selected "
                f"{len(selected_partition_urls)} sitemap resources, "
                f"discovered "
                f"{discovered_page_count} unique page URLs, "
                f"and planned "
                f"{selected_target_count} page acquisitions. "
                f"Failed sitemap resources: "
                f"{failed_sitemap_count}."
            )

        else:
            discovery_label = (
                "Bounded sitemap discovery"
                if traversal_policy == "bounded"
                else "Full sitemap discovery"
            )

            answer = (
                f"{discovery_label} completed. "
                f"Resolved "
                f"{discovery.sitemap_count if discovery else 0} "
                "sitemap resources, "
                f"discovered "
                f"{discovered_page_count} unique page URLs, "
                f"and planned "
                f"{selected_target_count} page acquisitions. "
                f"Failed sitemap resources: "
                f"{failed_sitemap_count}."
            )
    

    else:
        answer = (
            "Sitemap resolved. "
            f"Discovered "
            f"{discovered_page_count} unique page URLs "
            f"and planned "
            f"{selected_target_count} page acquisitions. "
            f"Failed sitemap resources: "
            f"{failed_sitemap_count}."
        )

    if discovery is not None and discovery.page_url_count > 0 and not eligible_page_urls:
        answer += " No page URLs match the configured scope."
    elif excluded_by_scope_count:
        answer += (
            f" Excluded {excluded_by_scope_count} page URLs "
            "outside the configured scope."
        )

    # ------------------------------------------------------------------
    # Final diagnostics.
    # ------------------------------------------------------------------
    AGENTVERSE_LOGGER.info(
        LogDomain.TOOL_BAY,
        (
            "[🕷️ WEB CRAWLER]"
            "[SITE_MAP]"
            "[RESOLVED] "
            f"type={topology.source_type} "
            f"mode={discovery_mode} "
            f"root_source={root_evidence.source} "
            f"root_reused={root_evidence.reused} "
            f"partitions={len(topology.partitions)} "
            f"root_groups="
            f"{len(catalog.roots) if catalog else 0} "
            f"selected_partitions="
            f"{len(selected_partition_urls)} "
            f"sitemaps="
            f"{discovery.sitemap_count if discovery else 0} "
            f"failed_sitemaps="
            f"{failed_sitemap_count} "
            f"discovered_pages="
            f"{discovered_page_count} "
            f"planned_pages="
            f"{selected_target_count}"
        ),
    )

    return SitemapScopePlan(
        discovery=discovery,
        coverage=coverage,
        crawl_plan=crawl_plan,
        planning_result=result,
        answer=answer,
    )
