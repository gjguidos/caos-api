from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import perf_counter

from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.tools.web_crawler_tool.execution.crawler_cpu_executor import (
    crawler_cpu_executor,
)
from src.domains.agentverse.tools.web_crawler_tool.helpers.urls import (
    UrlValidationError,
    normalize_url,
)
from src.domains.agentverse.tools.web_crawler_tool.sitemap.models import (
    SitemapDiscoveryFailure,
    SitemapDiscoveryResult,
)
from src.domains.agentverse.tools.web_crawler_tool.sitemap.progress import (
    SitemapDiscoveryProgress,
    SitemapProgressCallback,
)
from src.domains.agentverse.tools.web_crawler_tool.sitemap.resolver import (
    SitemapResolver,
)
from src.domains.agentverse.tools.web_crawler_tool.sitemap.store import (
    SitemapStateStore,
)
from src.domains.agentverse.tools.web_crawler_tool.sitemap.types import (
    SitemapTraversalPolicy,
)
from src.domains.agentverse.tools.web_crawler_tool.sitemap.url_inventory_store import (
    SitemapUrlInventoryStore,
)


@dataclass(frozen=True)
class PendingSitemap:
    """
    One sitemap document waiting to be resolved during recursive
    sitemap discovery.
    """

    url: str
    depth: int
    parent_sitemap_url: str | None = None


def _normalize_page_urls(
    candidate_urls: list[str],
) -> set[str]:
    """
    Normalize and deduplicate a batch of discovered sitemap page URLs.

    Intended to run off the main asyncio event loop because large
    terminal sitemap documents may contain thousands of page URLs.
    """

    normalized_urls: set[str] = set()

    for candidate_url in candidate_urls:
        try:
            page_url = normalize_url(
                candidate_url
            )
        except UrlValidationError:
            continue

        normalized_urls.add(
            page_url
        )

    return normalized_urls


async def discover_sitemap_urls(
    *,
    root_url: str,
    resolver: SitemapResolver,
    store: SitemapStateStore | None,
    url_inventory_store: SitemapUrlInventoryStore | None = None,
    run_id: str | None = None,
    page_url_return_limit: int | None = None,
    traversal_policy: SitemapTraversalPolicy = "complete",
    max_sitemaps: int | None = None,
    freshness_ttl_seconds: int,
    cpu_semaphore: asyncio.Semaphore,
    start_urls: list[str] | None = None,
    start_urls_count_as_descendants: bool = False,
    max_depth: int = 10,
    max_concurrency: int = 8,
    progress_interval: int = 100,
    pre_resolved_urls: list[str] | None = None,
    on_progress: SitemapProgressCallback | None = None,
) -> SitemapDiscoveryResult:
    """
    Recursively discover page URLs from a sitemap tree.

    Sitemap indexes enqueue additional sitemap resources.

    Terminal sitemap/urlset documents contribute page URLs.

    Discovery uses the lightweight sitemap location parser rather than
    constructing rich SitemapTopology/SitemapEntry/SitemapResource models.

    This keeps recursive discovery focused on only the information it
    actually needs:

      sitemapindex -> child sitemap URLs
      urlset       -> page URLs

    Rich sitemap metadata is handled separately by the root/evidence path.
    Actual page/media/document evidence is acquired later by the normal
    crawler.

    Discovery is executed through a bounded worker pool so that large
    sitemap trees can be resolved concurrently without creating an
    unbounded number of HTTP requests or asyncio tasks.

    Progress reporting is decoupled from sitemap workers. A dedicated
    reporter task samples shared discovery state and exposes the most
    recently started active sitemap as current_url, allowing the client
    to display sitemap discovery without making crawler workers wait on
    operation/Redis/WebSocket telemetry.

    The returned sitemap URLs are normalized, deduplicated, and sorted.

    Page URLs are normalized and deduplicated but are not globally sorted.
    Deterministic crawl-plan candidate selection is handled by planning
    coverage independently of discovery order.

    The sitemap resource store caches sitemap evidence. The URL inventory
    store persists discovered page URLs for the current crawl run.
    """

    if max_concurrency < 1:
        raise ValueError(
            "max_concurrency must be greater than or equal to 1"
        )

    if max_depth < 0:
        raise ValueError(
            "max_depth must be greater than or equal to 0"
        )

    normalized_root_url = normalize_url(
        root_url
    )

    if not normalized_root_url:
        raise ValueError(
            "root_url could not be normalized"
        )
    
    if traversal_policy not in {
        "complete",
        "bounded",
    }:
        raise ValueError(
            "traversal_policy must be 'complete' or 'bounded'"
        )

    if (
        traversal_policy == "bounded"
        and max_sitemaps is None
    ):
        raise ValueError(
            "max_sitemaps is required when "
            "traversal_policy='bounded'"
        )

    if (
        max_sitemaps is not None
        and max_sitemaps < 1
    ):
        raise ValueError(
            "max_sitemaps must be greater than or equal to 1"
        )

    if url_inventory_store is not None and not run_id:
        raise ValueError(
            "run_id is required when URL inventory persistence is enabled"
        )

    # ------------------------------------------------------------------
    # Shared discovery state.
    #
    # All state mutation happens synchronously between await points on
    # the same event loop, so explicit asyncio locks are unnecessary.
    # ------------------------------------------------------------------
    queue: asyncio.Queue[PendingSitemap] = (
        asyncio.Queue()
    )

    seen_sitemaps: set[str] = set()
    resolved_sitemaps: set[str] = set()
    page_urls: set[str] = set()
    discovered_page_url_count = 0
    discovery_order = 0
    page_inventory_lock = asyncio.Lock()

    failures: list[SitemapDiscoveryFailure] = []

    max_depth_reached = 0
    processed_count = 0
    descendant_sitemaps_admitted = 0
    
    # ------------------------------------------------------------------
    # Ordered active sitemap set.
    #
    # dict preserves insertion order, so the last key is the most
    # recently started sitemap that is still active.
    #
    # This gives current_url useful semantics even though sitemap
    # discovery has multiple concurrent workers.
    # ------------------------------------------------------------------
    active_sitemaps: dict[str, None] = {}

    async def persist_page_urls(
        *,
        source_sitemap_url: str,
        urls: set[str],
    ) -> None:
        nonlocal discovered_page_url_count
        nonlocal discovery_order

        if not urls:
            return

        if url_inventory_store is None:
            page_urls.update(urls)
            discovered_page_url_count = len(page_urls)
            return

        ordered_urls = list(urls)

        async with page_inventory_lock:
            for offset in range(0, len(ordered_urls), 2_000):
                batch = ordered_urls[offset:offset + 2_000]
                inserted = await url_inventory_store.add_urls(
                    run_id=run_id,
                    source_sitemap_url=source_sitemap_url,
                    urls=batch,
                    starting_order=discovery_order,
                )
                discovery_order += len(batch)
                discovered_page_url_count += inserted

    for pre_resolved_url in pre_resolved_urls or []:
        sitemap_url = normalize_url(
            pre_resolved_url
        )

        seen_sitemaps.add(
            sitemap_url
        )

        resolved_sitemaps.add(
            sitemap_url
        )

    # ------------------------------------------------------------------
    # Active sitemap helper.
    # ------------------------------------------------------------------
    def latest_active_url() -> str | None:
        if not active_sitemaps:
            return None

        return next(
            reversed(
                active_sitemaps
            )
        )

    # ------------------------------------------------------------------
    # Progress helper.
    #
    # Progress reporting must not be capable of failing sitemap
    # discovery itself. Cancellation is still propagated normally.
    # ------------------------------------------------------------------
    async def emit_progress(
        *,
        current_url: str | None = None,
    ) -> None:
        if on_progress is None:
            return

        progress = SitemapDiscoveryProgress(
            stage="discovering_sitemaps",
            processed_sitemaps=processed_count,
            discovered_sitemaps=len(
                seen_sitemaps
            ),
            resolved_sitemaps=len(
                resolved_sitemaps
            ),
            failed_sitemaps=len(
                failures
            ),
            queued_sitemaps=queue.qsize(),
            active_sitemaps=len(
                active_sitemaps
            ),
            discovered_page_urls=discovered_page_url_count,
            current_url=(
                current_url
                or latest_active_url()
            ),
        )

        try:
            await on_progress(
                progress
            )

        except asyncio.CancelledError:
            raise

        except Exception as exc:
            AGENTVERSE_LOGGER.warning(
                LogDomain.TOOL_BAY,
                (
                    "[🕷️ WEB CRAWLER]"
                    "[SITE_MAP]"
                    "[PROGRESS_EMIT_FAILED] "
                    f"error={exc}"
                ),
            )

    # ------------------------------------------------------------------
    # Queue helper.
    #
    # A sitemap is marked as seen when it is queued rather than when a
    # worker starts processing it. This prevents duplicate queue entries
    # when the same child sitemap appears through multiple indexes.
    # ------------------------------------------------------------------
    def enqueue_sitemap(
        *,
        url: str,
        depth: int,
        parent_sitemap_url: str | None = None,
        is_traversal_anchor: bool = False,
    ) -> None:
        nonlocal max_depth_reached
        nonlocal descendant_sitemaps_admitted

        try:
            sitemap_url = normalize_url(
                url
            )

        except Exception as exc:
            failures.append(
                SitemapDiscoveryFailure(
                    url=str(url),
                    error=(
                        "Failed to normalize sitemap URL: "
                        f"{exc}"
                    ),
                    parent_sitemap_url=(
                        parent_sitemap_url
                    ),
                    depth=depth,
                )
            )

            return

        if not sitemap_url:
            return

        if sitemap_url in seen_sitemaps:
            return

        # --------------------------------------------------------------
        # Bounded traversal admission.
        #
        # Anchors do NOT consume max_sitemaps.
        # Descendant sitemap resources DO.
        # --------------------------------------------------------------
        if (
            not is_traversal_anchor
            and traversal_policy == "bounded"
            and max_sitemaps is not None
            and descendant_sitemaps_admitted >= max_sitemaps
        ):
            return

        # --------------------------------------------------------------
        # Preserve existing discovery semantics.
        # --------------------------------------------------------------
        seen_sitemaps.add(
            sitemap_url
        )

        max_depth_reached = max(
            max_depth_reached,
            depth,
        )

        if depth > max_depth:
            failures.append(
                SitemapDiscoveryFailure(
                    url=sitemap_url,
                    error=(
                        "Maximum sitemap discovery "
                        f"depth exceeded ({max_depth})."
                    ),
                    parent_sitemap_url=(
                        parent_sitemap_url
                    ),
                    depth=depth,
                )
            )

            AGENTVERSE_LOGGER.warning(
                LogDomain.TOOL_BAY,
                (
                    "[🕷️ WEB CRAWLER]"
                    "[SITE_MAP]"
                    "[DISCOVERY_DEPTH_EXCEEDED] "
                    f"url={sitemap_url} "
                    f"depth={depth} "
                    f"max_depth={max_depth}"
                ),
            )

            return

        # --------------------------------------------------------------
        # Only actual descendants consume the bounded traversal budget.
        # --------------------------------------------------------------
        if not is_traversal_anchor:
            descendant_sitemaps_admitted += 1

        queue.put_nowait(
            PendingSitemap(
                url=sitemap_url,
                depth=depth,
                parent_sitemap_url=(
                    parent_sitemap_url
                ),
            )
        )

    # ------------------------------------------------------------------
    # Seed discovery.
    #
    # start_urls allows the same recursive discovery engine to operate
    # from either:
    #
    #   - the root sitemap itself
    #   - root sitemap-index descendants
    #   - selected sitemap anchors
    #
    # Whether the supplied start URLs consume the bounded traversal
    # budget is controlled explicitly by
    # start_urls_count_as_descendants.
    # ------------------------------------------------------------------
    initial_urls = (
        start_urls
        if start_urls
        else [normalized_root_url]
    )

    for initial_url in initial_urls:
        enqueue_sitemap(
            url=initial_url,
            depth=0,
            is_traversal_anchor=(
                not start_urls_count_as_descendants
            ),
        )

    AGENTVERSE_LOGGER.info(
        LogDomain.TOOL_BAY,
        (
            "[🕷️ WEB CRAWLER]"
            "[SITE_MAP]"
            "[DISCOVERY_START] "
            f"root={normalized_root_url} "
            f"initial={queue.qsize()} "
            f"traversal_policy={traversal_policy} "
            f"max_sitemaps={max_sitemaps} "
            f"concurrency={max_concurrency} "
            f"max_depth={max_depth} "
            "parser=locations"
        ),
    )

    # ------------------------------------------------------------------
    # Worker.
    # ------------------------------------------------------------------
    async def worker(
        worker_id: int,
    ) -> None:
        nonlocal processed_count

        while True:
            current = await queue.get()

            # ----------------------------------------------------------
            # Mark this sitemap as active immediately after the worker
            # receives it.
            #
            # current_url therefore means:
            #
            #   most recently surfaced active sitemap resource
            #
            # rather than:
            #
            #   the only resource currently being processed
            #
            # active_sitemaps gives the actual concurrency count.
            # ----------------------------------------------------------
            active_sitemaps[
                current.url
            ] = None

            try:
                # ------------------------------------------------------
                # Worker execution is intentionally independent of
                # progress publication.
                #
                # active_sitemaps already exposes the current resource
                # to the dedicated progress reporter below.
                # ------------------------------------------------------

                # ------------------------------------------------------
                # Recursive discovery deliberately uses the lightweight
                # location-only sitemap parser.
                #
                # We need only:
                #
                #   sitemapindex -> child sitemap URLs
                #   urlset       -> page URLs
                #
                # Rich SitemapEntry/SitemapResource models are not
                # constructed here.
                # ------------------------------------------------------
                resolved = await resolver.resolve_locations(
                    current.url
                )

                # ------------------------------------------------------
                # We are not supplying HTTP validators on this benchmark
                # path, so a 304 is not expected.
                #
                # Once lightweight sitemap state is implemented this
                # branch will reuse the cached location document.
                # ------------------------------------------------------
                if resolved.not_modified:
                    raise ValueError(
                        "Lightweight sitemap discovery received HTTP 304 "
                        "without lightweight cached discovery state: "
                        f"{current.url}"
                    )

                document = resolved.document

                if document is None:
                    raise ValueError(
                        "Sitemap location resolution produced no document: "
                        f"{current.url}"
                    )

                resolved_sitemaps.add(
                    current.url
                )

                # ------------------------------------------------------
                # Sitemap index:
                # enqueue child sitemap resources.
                # ------------------------------------------------------
                if document.source_type == "sitemap_index":
                    for sitemap_url in document.sitemap_urls:
                        enqueue_sitemap(
                            url=sitemap_url,
                            depth=(
                                current.depth
                                + 1
                            ),
                            parent_sitemap_url=(
                                current.url
                            ),
                            is_traversal_anchor=False,
                        )

                # ------------------------------------------------------
                # Terminal sitemap/urlset:
                # collect actual page targets.
                #
                # URL normalization shares the same sitemap CPU budget
                # as XML parsing.
                #
                # Split timing into:
                #
                #   cpu_wait -> waiting for the shared CPU semaphore
                #   cpu_work -> process-pool normalization execution
                # ------------------------------------------------------
                else:
                    normalize_wait_started = (
                        perf_counter()
                    )

                    async with cpu_semaphore:
                        normalize_cpu_wait_seconds = (
                            perf_counter()
                            - normalize_wait_started
                        )

                        normalize_work_started = (
                            perf_counter()
                        )

                        normalized_page_urls = await crawler_cpu_executor.run(
                            _normalize_page_urls,
                            document.page_urls,
                        )

                        normalize_cpu_work_seconds = (
                            perf_counter()
                            - normalize_work_started
                        )

                    normalize_seconds = (
                        normalize_cpu_wait_seconds
                        + normalize_cpu_work_seconds
                    )

                    if (
                        normalize_seconds >= 0.25
                        or normalize_cpu_wait_seconds >= 0.25
                        or normalize_cpu_work_seconds >= 0.25
                    ):
                        AGENTVERSE_LOGGER.info(
                            LogDomain.TOOL_BAY,
                            (
                                "[🕷️ WEB CRAWLER]"
                                "[SITE_MAP]"
                                "[NORMALIZE_TIMING] "
                                f"url={current.url} "
                                f"total={normalize_seconds:.3f}s "
                                f"cpu_wait="
                                f"{normalize_cpu_wait_seconds:.3f}s "
                                f"cpu_work="
                                f"{normalize_cpu_work_seconds:.3f}s "
                                f"input={len(document.page_urls)} "
                                f"output={len(normalized_page_urls)}"
                            ),
                        )

                    await persist_page_urls(
                        source_sitemap_url=current.url,
                        urls=normalized_page_urls,
                    )

            except asyncio.CancelledError:
                raise

            except Exception as exc:
                failures.append(
                    SitemapDiscoveryFailure(
                        url=current.url,
                        error=str(
                            exc
                        ),
                        parent_sitemap_url=(
                            current.parent_sitemap_url
                        ),
                        depth=current.depth,
                    )
                )

                AGENTVERSE_LOGGER.warning(
                    LogDomain.TOOL_BAY,
                    (
                        "[🕷️ WEB CRAWLER]"
                        "[SITE_MAP]"
                        "[DISCOVERY_FAILED] "
                        f"url={current.url} "
                        f"depth={current.depth} "
                        f"worker={worker_id} "
                        f"error={exc}"
                    ),
                )

            finally:
                # ------------------------------------------------------
                # This worker is no longer actively processing the
                # sitemap resource.
                # ------------------------------------------------------
                active_sitemaps.pop(
                    current.url,
                    None,
                )

                processed_count += 1

                # ------------------------------------------------------
                # Complete queue accounting before external progress
                # publication. This prevents telemetry problems from
                # leaving queue.join() blocked indefinitely.
                # ------------------------------------------------------
                queue.task_done()

                if (
                    progress_interval > 0
                    and processed_count
                    % progress_interval
                    == 0
                ):
                    AGENTVERSE_LOGGER.info(
                        LogDomain.TOOL_BAY,
                        (
                            "[🕷️ WEB CRAWLER]"
                            "[SITE_MAP]"
                            "[DISCOVERY_PROGRESS] "
                            f"processed={processed_count} "
                            f"discovered_sitemaps="
                            f"{len(seen_sitemaps)} "
                            f"resolved_sitemaps="
                            f"{len(resolved_sitemaps)} "
                            f"queued={queue.qsize()} "
                            f"active={len(active_sitemaps)} "
                            f"pages={discovered_page_url_count} "
                            f"failed={len(failures)}"
                        ),
                    )

                # ------------------------------------------------------
                # Do not publish progress from the worker hot path.
                #
                # The dedicated reporter samples the updated counters,
                # queue size, and active sitemap set independently.
                # ------------------------------------------------------

    # ------------------------------------------------------------------
    # Dedicated progress reporter.
    #
    # Workers never await operation/Redis/WebSocket telemetry. The
    # reporter samples shared discovery state at a bounded cadence.
    # ------------------------------------------------------------------
    async def progress_reporter() -> None:
        try:
            while True:
                await emit_progress()

                await asyncio.sleep(
                    0.5
                )

        except asyncio.CancelledError:
            raise

    # ------------------------------------------------------------------
    # Start bounded worker pool.
    # ------------------------------------------------------------------
    workers = [
        asyncio.create_task(
            worker(
                worker_id
            ),
            name=(
                "web-crawler-sitemap-discovery-"
                f"{worker_id}"
            ),
        )
        for worker_id in range(
            max_concurrency
        )
    ]

    progress_task: asyncio.Task[None] | None = None

    if on_progress is not None:
        progress_task = asyncio.create_task(
            progress_reporter(),
            name="web-crawler-sitemap-progress",
        )

    try:
        # --------------------------------------------------------------
        # queue.join() remains valid while workers recursively enqueue
        # child sitemap resources because every put() increments the
        # queue's unfinished-task count.
        # --------------------------------------------------------------
        await queue.join()

        # --------------------------------------------------------------
        # Stop periodic publication before emitting the authoritative
        # terminal snapshot. This avoids overlapping terminal events.
        # --------------------------------------------------------------
        if progress_task is not None:
            progress_task.cancel()

            await asyncio.gather(
                progress_task,
                return_exceptions=True,
            )

            progress_task = None

        # --------------------------------------------------------------
        # Authoritative terminal discovery snapshot.
        #
        # At this point all queued sitemap work is complete:
        #
        #   queued = 0
        #   active = 0
        #   processed = terminal worker count
        #
        # The operation bridge can therefore identify
        # discovery_complete deterministically.
        # --------------------------------------------------------------
        await emit_progress()

    except asyncio.CancelledError:
        AGENTVERSE_LOGGER.warning(
            LogDomain.TOOL_BAY,
            (
                "[🕷️ WEB CRAWLER]"
                "[SITE_MAP]"
                "[DISCOVERY_CANCELLED] "
                f"processed={processed_count} "
                f"discovered_sitemaps="
                f"{len(seen_sitemaps)} "
                f"resolved_sitemaps="
                f"{len(resolved_sitemaps)} "
                f"queued={queue.qsize()} "
                f"active={len(active_sitemaps)} "
                f"pages={discovered_page_url_count} "
                f"failed={len(failures)}"
            ),
        )

        raise

    finally:
        # --------------------------------------------------------------
        # Ensure the independent progress task cannot survive discovery
        # cancellation or failure.
        # --------------------------------------------------------------
        if progress_task is not None:
            progress_task.cancel()

            await asyncio.gather(
                progress_task,
                return_exceptions=True,
            )

        # --------------------------------------------------------------
        # Workers block indefinitely on queue.get() after discovery has
        # completed, so explicitly cancel and collect them.
        # --------------------------------------------------------------
        for worker_task in workers:
            worker_task.cancel()

        await asyncio.gather(
            *workers,
            return_exceptions=True,
        )

    # ------------------------------------------------------------------
    # Finalize discovery output.
    #
    # Sitemap URLs remain sorted because the sitemap collection is small
    # and deterministic ordering is useful.
    #
    # Page URLs are already normalized and deduplicated in `page_urls`.
    # Crawl planning no longer requires global page ordering because
    # coverage retains deterministic bounded candidates independently of
    # discovery order.
    #
    # Avoid sorting millions of page URLs on the asyncio event loop.
    # ------------------------------------------------------------------
    finalize_started = perf_counter()

    sitemap_sort_started = perf_counter()

    discovered_sitemaps = sorted(
        seen_sitemaps
    )

    sitemap_sort_seconds = (
        perf_counter()
        - sitemap_sort_started
    )

    page_materialize_started = perf_counter()

    if url_inventory_store is not None:
        discovered_pages = [
            record.url
            async for record in url_inventory_store.iter_urls(
                run_id=run_id,
                status="discovered",
                limit=page_url_return_limit,
            )
        ]
    else:
        discovered_pages = list(page_urls)

    page_materialize_seconds = (
        perf_counter()
        - page_materialize_started
    )

    finalize_seconds = (
        perf_counter()
        - finalize_started
    )

    AGENTVERSE_LOGGER.info(
        LogDomain.TOOL_BAY,
        (
            "[🕷️ WEB CRAWLER]"
            "[SITE_MAP]"
            "[FINALIZE_TIMING] "
            f"sitemaps={len(discovered_sitemaps)} "
            f"pages={len(discovered_pages)} "
            f"sitemap_sort={sitemap_sort_seconds:.3f}s "
            f"page_materialize={page_materialize_seconds:.3f}s "
            f"total={finalize_seconds:.3f}s"
        ),
    )

    result = SitemapDiscoveryResult(
        root_url=normalized_root_url,
        sitemap_urls=discovered_sitemaps,
        page_urls=discovered_pages,
        sitemap_count=len(
            discovered_sitemaps
        ),
        page_url_count=discovered_page_url_count,
        max_depth_reached=(
            max_depth_reached
        ),
        failed_sitemaps=failures,
        failed_sitemap_count=len(
            failures
        ),
    )

    AGENTVERSE_LOGGER.info(
        LogDomain.TOOL_BAY,
        (
            "[🕷️ WEB CRAWLER]"
            "[SITE_MAP]"
            "[DISCOVERY_COMPLETE] "
            f"processed={processed_count} "
            f"sitemaps={result.sitemap_count} "
            f"resolved={len(resolved_sitemaps)} "
            f"pages={result.page_url_count} "
            f"failed={result.failed_sitemap_count} "
            f"max_depth={result.max_depth_reached} "
            f"concurrency={max_concurrency} "
            "parser=locations"
        ),
    )

    return result
