from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

from bs4 import BeautifulSoup

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
from src.domains.agentverse.tools.web_crawler_tool.engine.helpers import (
    is_document_url,
    strip_fragment,
)
from src.domains.agentverse.tools.web_crawler_tool.fetchers.models import (
    FetchedPage,
)
from src.domains.agentverse.tools.web_crawler_tool.helpers.parser import (
    extract_links,
)
from src.domains.agentverse.tools.web_crawler_tool.helpers.urls import (
    same_domain,
)
from src.domains.agentverse.tools.web_crawler_tool.reports.crawl_graph import (
    CrawlGraphBuilder,
)
from src.domains.agentverse.tools.web_crawler_tool.security.audit import (
    audit_error,
    audit_from_verdict,
    audit_skip,
)
from src.domains.agentverse.tools.web_crawler_tool.security.policy import (
    evaluate_url_security,
)
from src.domains.agentverse.tools.web_crawler_tool.types import (
    CrawlAcquisitionProgress,
    CrawlResult,
    SecurityAuditEntry,
)

CrawlProgressCallback = Callable[
    [CrawlAcquisitionProgress],
    Awaitable[None],
]

PageFetchedCallback = Callable[
    [str, int, int, FetchedPage],
    Awaitable[None],
]


class CrawlEngine:
    async def crawl(
        self,
        *,
        agent: AgentInterface | None,
        start_url: str,
        target_urls: list[str] | None = None,
        mode: str,
        fetcher: Any,
        auth_context: CrawlAuthContext | None,
        max_pages: int,
        max_depth: int,
        crawl_strategy: str,
        same_domain_only: bool,
        include_links: bool,
        rate_limit_delay_ms: int,
        security: WebCrawlerSecurityConfig,
        context: dict[str, Any] | None = None,
        on_progress: CrawlProgressCallback | None = None,
        on_page_fetched: PageFetchedCallback | None = None,
        acquisition_concurrency: int = 4,
        discover_links: bool = False,
        progress_interval_seconds: float = 0.5,
        page_timeout_seconds: float = 60.0,
    ) -> CrawlResult:
        if acquisition_concurrency < 1:
            raise ValueError("acquisition_concurrency must be positive.")
        if progress_interval_seconds < 0:
            raise ValueError("progress_interval_seconds must be non-negative.")
        if rate_limit_delay_ms < 0:
            raise ValueError("rate_limit_delay_ms must be non-negative.")
        if page_timeout_seconds <= 0:
            raise ValueError(
                "page_timeout_seconds must be positive."
            )

        if mode != "static":
            raise ValueError(
                "Raw acquisition currently supports static mode only."
            )

        if target_urls is None:
            raise ValueError(
                "Raw acquisition requires explicit target_urls."
            )

        if on_page_fetched is None:
            raise ValueError(
                "on_page_fetched is required for raw acquisition."
            )

        crawl_scope_url = start_url
        explicit_targets = True

        # Preserve plan order while deduplicating normalized URLs.
        initial_urls = list(
            dict.fromkeys(
                clean_url
                for url in target_urls
                if (clean_url := strip_fragment(url))
            )
        )

        visited: set[str] = set()
        queued: set[str] = set(initial_urls)
        queue: deque[tuple[str, int]] = deque(
            (url, 0)
            for url in initial_urls
        )

        successful = 0
        failed = 0
        errors: list[str] = []
        security_audit: list[
            SecurityAuditEntry
        ] = []

        graph = CrawlGraphBuilder(
            start_url=start_url
        )

        # Only explicit static targets are independent of frontier ordering
        # and mutable browser sessions. A fixed pool bounds task count.
        worker_count = (
            min(acquisition_concurrency, len(initial_urls), max(0, max_pages))
            if explicit_targets and mode == "static" and not discover_links
            else 1
        )

        loop = asyncio.get_running_loop()
        pacing_lock = asyncio.Lock()
        progress_lock = asyncio.Lock()
        next_request_at = 0.0
        last_progress_at: float | None = None
        active = 0
        processed = 0
        last_url = start_url
        last_depth = 0
        next_order = 0

        async def pace_request() -> None:
            nonlocal next_request_at
            # Shared across this operation: workers do not each receive
            # an independent rate allowance. Fetch latency overlaps the gap.
            async with pacing_lock:
                delay = next_request_at - loop.time()
                if delay > 0:
                    await asyncio.sleep(delay)
                next_request_at = loop.time() + rate_limit_delay_ms / 1000

        async def report_progress(*, force: bool = False) -> None:
            nonlocal last_progress_at
            if on_progress is None:
                return
            async with progress_lock:
                now = loop.time()
                if (
                    not force
                    and last_progress_at is not None
                    and now - last_progress_at < progress_interval_seconds
                ):
                    return
                await on_progress(
                    CrawlAcquisitionProgress(
                        current=successful + failed,
                        limit=max_pages,
                        successful=successful,
                        failed=failed,
                        queued=len(queue),
                        visited=len(visited),
                        current_url=last_url,
                        depth=last_depth,
                        metadata={
                            "active": active,
                            "processed_targets": processed,
                            "skipped_targets": (
                                processed
                                - successful
                                - failed
                            ),
                            "acquisition_concurrency": worker_count,
                            "acquisition_complete": force,
                        },
                    )
                )
                last_progress_at = loop.time()

        async def process_url(current_url: str, depth: int, order: int) -> None:
            nonlocal successful, failed, crawl_scope_url

            current_url = strip_fragment(
                current_url
            )

            queued.discard(current_url)

            if current_url in visited:
                return

            if self._should_skip_outside_domain(
                current_url=current_url,
                crawl_scope_url=crawl_scope_url,
                same_domain_only=same_domain_only,
            ):
                reason = (
                    "Skipped because URL is outside "
                    "the start domain."
                )

                errors.append(
                    f"{current_url}: {reason}"
                )

                graph.mark_skipped(
                    url=current_url,
                    depth=depth,
                    reason=reason,
                )

                security_audit.append(
                    audit_skip(
                        url=current_url,
                        stage="same_domain_policy",
                        reason=reason,
                        rule_id=(
                            "crawl.same_domain_only"
                        ),
                        details={
                            "start_url": start_url,
                            "depth": depth,
                        },
                    )
                )

                return

            verdict = evaluate_url_security(
                current_url,
                security=security,
                start_url=crawl_scope_url,
            )

            security_audit.append(
                audit_from_verdict(
                    url=current_url,
                    stage="queue_validation",
                    verdict=verdict,
                    details={
                        "depth": depth,
                    },
                )
            )

            if not verdict.allowed:
                reason = (
                    "blocked by security policy: "
                    f"{verdict.reason}"
                )

                errors.append(
                    f"{current_url}: {reason}"
                )

                graph.mark_blocked(
                    url=current_url,
                    depth=depth,
                    reason=reason,
                )

                return

            visited.add(current_url)

            if explicit_targets:
                await pace_request()

            page, fetch_exception = await self._fetch_page(
                fetcher=fetcher,
                current_url=current_url,
                auth_context=auth_context,
                agent=agent,
                context=context,
                errors=errors,
                security_audit=security_audit,
                timeout_seconds=page_timeout_seconds,
            )

            graph.mark_visited(
                url=current_url,
                depth=depth,
                title="",
                status_code=page.status_code,
                content_type=page.content_type,
                error=page.error,
            )

            if (
                page.error
                and not fetch_exception
            ):
                errors.append(
                    f"{current_url}: {page.error}"
                )

                security_audit.append(
                    audit_error(
                        url=current_url,
                        stage="fetch_response",
                        reason=page.error,
                        rule_id=(
                            f"fetch.{page.error}"
                        ),
                        details={
                            "status_code": (
                                page.status_code
                            ),
                            "content_type": (
                                page.content_type
                            ),
                        },
                    )
                )

            if not page.error:
                graph.mark_edge_followed(
                    target_url=current_url
                )

            await on_page_fetched(
                current_url,
                depth,
                order,
                page,
            )

            if page.error:
                failed += 1
            else:
                successful += 1
                if discover_links and depth < max_depth:
                    content_type = page.content_type.lower()
                    if "html" in content_type and page.body:
                        html = page.body.decode(
                            page.encoding or "utf-8",
                            errors="replace",
                        )
                        soup = BeautifulSoup(
                            html,
                            "html.parser",
                        )
                        links = extract_links(
                            soup=soup,
                            base_url=page.final_url or current_url,
                        )
                        queue.extend(
                            self._discover_links(
                                links=links,
                                current_url=current_url,
                                depth=depth,
                                start_url=start_url,
                                crawl_scope_url=crawl_scope_url,
                                same_domain_only=same_domain_only,
                                visited=visited,
                                queued=queued,
                                graph=graph,
                                security=security,
                                errors=errors,
                                security_audit=security_audit,
                            )
                        )
        async def worker() -> None:
            nonlocal active, processed, last_url, last_depth, next_order
            while queue:
                # Reserve capacity before awaiting so in-flight requests
                # cannot overshoot max_pages. Skips release their capacity.
                if successful + failed + active >= max_pages:
                    return
                current_url, depth = queue.popleft()
                order = next_order
                next_order += 1
                active += 1
                try:
                    await process_url(current_url, depth, order)
                finally:
                    active -= 1
                processed += 1
                last_url, last_depth = current_url, depth
                await report_progress()


        if worker_count > 1:
            # TaskGroup cancels and joins siblings on failure/cancellation.
            async with asyncio.TaskGroup() as group:
                for _ in range(worker_count):
                    group.create_task(worker())
        elif worker_count == 1:
            await worker()

        return CrawlResult(
            start_url=start_url,
            mode=mode,
            page_count=successful + failed,
            pages=[],
            errors=errors,
            security_audit=security_audit,
            crawl_graph=graph.build(),
        )

    async def _fetch_page(
        self,
        *,
        fetcher: Any,
        current_url: str,
        auth_context: CrawlAuthContext | None,
        agent: Any | None,
        context: dict[str, Any] | None,
        errors: list[str],
        security_audit: list[SecurityAuditEntry],
        timeout_seconds: float,
    ) -> tuple[FetchedPage, bool]:
        fetch_exception = False
        try:
            async with asyncio.timeout(timeout_seconds):
                page = await fetcher.fetch(
                    current_url,
                    auth_context=auth_context,
                    agent=agent,
                    context=context,
                )


        except asyncio.CancelledError:
            raise

        except TimeoutError:
            fetch_exception = True

            page = FetchedPage(
                url=current_url,
                final_url=current_url,
                status_code=0,
                content_type="",
                encoding=None,
                body=b"",
                error=(
                    f"URL timed out after "
                    f"{timeout_seconds:.1f}s"
                ),
            )

            errors.append(
                f"{current_url}: {page.error}"
            )

            security_audit.append(
                audit_error(
                    url=current_url,
                    stage="fetch_timeout",
                    reason=page.error or "URL fetch timed out.",
                    rule_id="fetch.timeout",
                    details={
                        "timeout_seconds": timeout_seconds,
                    },
                )
            )

        except Exception as exc:
            fetch_exception = True

            AGENTVERSE_LOGGER.error(
                LogDomain.TOOL_BAY,
                (
                    "[🕷️ WEB CRAWLER][PAGE_FETCH][CAPTURED_ERROR] "
                    f"url={current_url} "
                    f"type={type(exc).__name__} "
                    f"error={exc}"
                ),
            )

            page = FetchedPage(
                url=current_url,
                final_url=current_url,
                status_code=0,
                content_type="",
                encoding=None,
                body=b"",
                error=f"{type(exc).__name__}: {exc}",
            )

            errors.append(
                f"{current_url}: {page.error}"
            )

            security_audit.append(
                audit_error(
                    url=current_url,
                    stage="fetch_response",
                    reason=page.error or "Fetch failed.",
                    rule_id="fetch.exception",
                    details={
                        "exception_type": type(exc).__name__,
                    },
                )
            )

        return page, fetch_exception

    def _discover_links(
        self,
        *,
        links: list[str],
        current_url: str,
        depth: int,
        start_url: str,
        crawl_scope_url: str,
        same_domain_only: bool,
        visited: set[str],
        queued: set[str],
        graph: CrawlGraphBuilder,
        security: WebCrawlerSecurityConfig,
        errors: list[str],
        security_audit: list[
            SecurityAuditEntry
        ],
    ) -> list[tuple[str, int]]:
        discovered_links: list[
            tuple[str, int]
        ] = []

        next_depth = depth + 1

        for link in links:
            clean_link = strip_fragment(
                link
            )

            if not clean_link:
                continue

            if clean_link == start_url:
                graph.add_edge(
                    source_url=current_url,
                    target_url=clean_link,
                    depth=next_depth,
                    status="duplicate",
                    reason=(
                        "Discovered link points "
                        "back to start URL."
                    ),
                )
                continue

            if is_document_url(
                clean_link
            ):
                graph.add_edge(
                    source_url=current_url,
                    target_url=clean_link,
                    depth=next_depth,
                    status="skipped",
                    reason=(
                        "document_url_not_crawled"
                    ),
                    metadata={
                        "resource_type": (
                            "document"
                        ),
                        "policy": (
                            "inventory_only"
                        ),
                    },
                )

                graph.mark_skipped(
                    url=clean_link,
                    depth=next_depth,
                    reason=(
                        "Document URL discovered "
                        "but not crawled as a page."
                    ),
                    discovered_from=(
                        current_url
                    ),
                )

                continue

            if (
                clean_link in visited
                or clean_link in queued
            ):
                graph.add_edge(
                    source_url=current_url,
                    target_url=clean_link,
                    depth=next_depth,
                    status="duplicate",
                    reason=(
                        "URL already visited "
                        "or queued."
                    ),
                )
                continue

            if (
                same_domain_only
                and not same_domain(
                    clean_link,
                    crawl_scope_url,
                )
            ):
                graph.add_edge(
                    source_url=current_url,
                    target_url=clean_link,
                    depth=next_depth,
                    status="skipped",
                    reason=(
                        "Outside start domain."
                    ),
                )

                graph.mark_skipped(
                    url=clean_link,
                    depth=next_depth,
                    reason=(
                        "Outside start domain."
                    ),
                    discovered_from=(
                        current_url
                    ),
                )

                security_audit.append(
                    audit_skip(
                        url=clean_link,
                        stage=(
                            "same_domain_policy"
                        ),
                        reason=(
                            "Discovered link "
                            "skipped because it is "
                            "outside the start "
                            "domain."
                        ),
                        rule_id=(
                            "crawl.same_domain_only"
                        ),
                        details={
                            "source_url": (
                                current_url
                            ),
                            "depth": (
                                next_depth
                            ),
                        },
                    )
                )

                continue

            verdict = evaluate_url_security(
                clean_link,
                security=security,
                start_url=crawl_scope_url,
            )

            security_audit.append(
                audit_from_verdict(
                    url=clean_link,
                    stage=(
                        "discovered_link_validation"
                    ),
                    verdict=verdict,
                    details={
                        "source_url": (
                            current_url
                        ),
                        "depth": next_depth,
                    },
                )
            )

            if not verdict.allowed:
                reason = (
                    "blocked by security policy: "
                    f"{verdict.reason}"
                )

                graph.add_edge(
                    source_url=current_url,
                    target_url=clean_link,
                    depth=next_depth,
                    status="blocked",
                    reason=reason,
                )

                graph.mark_blocked(
                    url=clean_link,
                    depth=next_depth,
                    reason=reason,
                    discovered_from=(
                        current_url
                    ),
                )

                errors.append(
                    f"{clean_link}: {reason}"
                )

                continue

            graph.add_edge(
                source_url=current_url,
                target_url=clean_link,
                depth=next_depth,
                status="discovered",
            )

            graph.add_node(
                url=clean_link,
                depth=next_depth,
                status="queued",
                discovered_from=current_url,
            )

            discovered_links.append(
                (
                    clean_link,
                    next_depth,
                )
            )

            queued.add(clean_link)

        return discovered_links

    @staticmethod
    def _should_skip_outside_domain(
        *,
        current_url: str,
        crawl_scope_url: str,
        same_domain_only: bool,
    ) -> bool:
        return (
            same_domain_only
            and not same_domain(
                current_url,
                crawl_scope_url,
            )
        )
    
