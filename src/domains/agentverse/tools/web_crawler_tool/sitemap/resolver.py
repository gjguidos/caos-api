from __future__ import annotations

import asyncio
from dataclasses import dataclass
from time import perf_counter

from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.tools.web_crawler_tool.sitemap.fetcher import (
    SitemapFetcher,
    SitemapFetchResult,
)
from src.domains.agentverse.tools.web_crawler_tool.sitemap.models import (
    SitemapTopology,
)
from src.domains.agentverse.tools.web_crawler_tool.sitemap.parser import (
    SitemapLocationDocument,
    parse_sitemap_locations,
    parse_sitemap_xml,
)


@dataclass(frozen=True)
class ResolvedSitemapLocations:
    document: SitemapLocationDocument | None

    fetch: SitemapFetchResult

    source_url: str
    final_url: str

    not_modified: bool = False


@dataclass(frozen=True)
class ResolvedSitemap:
    topology: SitemapTopology | None

    fetch: SitemapFetchResult

    source_url: str
    final_url: str

    not_modified: bool = False


class SitemapResolver:
    """
    Resolves one sitemap resource.

    Responsibilities:
      - acquire one sitemap document
      - preserve HTTP validators
      - parse XML into normalized topology/location evidence

    Does not:
      - recurse through sitemap indexes
      - resolve selections
      - resolve multiple partitions
      - update sitemap/site state

    CPU-heavy sitemap processing is bounded by a shared semaphore owned
    by the sitemap execution scope. This allows XML parsing and other
    sitemap CPU work, such as URL normalization, to share one concurrency
    budget.
    """

    def __init__(
        self,
        *,
        fetcher: SitemapFetcher,
        cpu_semaphore: asyncio.Semaphore,
    ) -> None:
        self.fetcher = fetcher
        self._cpu_semaphore = cpu_semaphore

    async def _parse_locations(
        self,
        *,
        xml: str,
    ) -> tuple[
        SitemapLocationDocument,
        float,
        float,
    ]:
        wait_started = perf_counter()

        async with self._cpu_semaphore:
            cpu_wait_seconds = (
                perf_counter()
                - wait_started
            )

            work_started = perf_counter()

            document = await asyncio.to_thread(
                parse_sitemap_locations,
                xml=xml,
            )

            cpu_work_seconds = (
                perf_counter()
                - work_started
            )

        return (
            document,
            cpu_wait_seconds,
            cpu_work_seconds,
        )

    async def _parse_topology(
        self,
        *,
        xml: str,
        source_url: str,
    ) -> tuple[
        SitemapTopology,
        float,
        float,
    ]:
        wait_started = perf_counter()

        async with self._cpu_semaphore:
            cpu_wait_seconds = (
                perf_counter()
                - wait_started
            )

            work_started = perf_counter()

            topology = await asyncio.to_thread(
                parse_sitemap_xml,
                xml=xml,
                source_url=source_url,
            )

            cpu_work_seconds = (
                perf_counter()
                - work_started
            )

        return (
            topology,
            cpu_wait_seconds,
            cpu_work_seconds,
        )

    async def resolve(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> ResolvedSitemap:
        """
        Resolve one sitemap resource using the rich parser.

        Used for root/catalog/evidence scenarios where the full
        SitemapTopology is required.
        """

        # --------------------------------------------------------------
        # Network acquisition.
        # --------------------------------------------------------------
        fetch_started = perf_counter()

        fetched = await self.fetcher.fetch(
            url,
            etag=etag,
            last_modified=last_modified,
        )

        fetch_seconds = (
            perf_counter()
            - fetch_started
        )

        if fetched.not_modified:
            _log_resolve_timing(
                mode="rich",
                url=fetched.final_url,
                fetch_seconds=fetch_seconds,
                parse_seconds=None,
                cpu_wait_seconds=None,
                cpu_work_seconds=None,
                source_type=None,
                sitemap_count=None,
                page_count=None,
                not_modified=True,
            )

            return ResolvedSitemap(
                topology=None,
                fetch=fetched,
                source_url=url,
                final_url=fetched.final_url,
                not_modified=True,
            )

        if not fetched.text:
            raise ValueError(
                f"Sitemap returned no content: {fetched.final_url}"
            )

        # --------------------------------------------------------------
        # Rich XML parsing + topology/model construction.
        #
        # Uses the shared sitemap CPU budget.
        # --------------------------------------------------------------

        (
            topology,
            cpu_wait_seconds,
            cpu_work_seconds,
        ) = await self._parse_topology(
            xml=fetched.text,
            source_url=fetched.final_url,
        )

        parse_seconds = (
            cpu_wait_seconds
            + cpu_work_seconds
        )

        _log_resolve_timing(
            mode="rich",
            url=fetched.final_url,
            fetch_seconds=fetch_seconds,
            parse_seconds=parse_seconds,
            source_type=topology.source_type,
            cpu_wait_seconds=cpu_wait_seconds,
            cpu_work_seconds=cpu_work_seconds,
            sitemap_count=(
                len(topology.partitions)
                if topology.source_type == "sitemap_index"
                else 0
            ),
            page_count=topology.total_urls,
            not_modified=False,
        )

        return ResolvedSitemap(
            topology=topology,
            fetch=fetched,
            source_url=url,
            final_url=fetched.final_url,
            not_modified=False,
        )

    async def resolve_locations(
        self,
        url: str,
        *,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> ResolvedSitemapLocations:
        """
        Resolve one sitemap resource using the lightweight location
        parser.

        Used during recursive sitemap discovery where only:

          sitemapindex -> child sitemap URLs
          urlset       -> page URLs

        are required.
        """

        # --------------------------------------------------------------
        # Network acquisition.
        # --------------------------------------------------------------
        fetch_started = perf_counter()

        fetched = await self.fetcher.fetch(
            url,
            etag=etag,
            last_modified=last_modified,
        )

        fetch_seconds = (
            perf_counter()
            - fetch_started
        )

        if fetched.not_modified:
            _log_resolve_timing(
                mode="locations",
                url=fetched.final_url,
                fetch_seconds=fetch_seconds,
                parse_seconds=None,
                cpu_wait_seconds=None,
                cpu_work_seconds=None,
                source_type=None,
                sitemap_count=None,
                page_count=None,
                not_modified=True,
            )

            return ResolvedSitemapLocations(
                document=None,
                fetch=fetched,
                source_url=url,
                final_url=fetched.final_url,
                not_modified=True,
            )

        if not fetched.text:
            raise ValueError(
                f"Sitemap returned no content: {fetched.final_url}"
            )

        # --------------------------------------------------------------
        # Lightweight XML parsing.
        #
        # Uses the same shared CPU semaphore as rich parsing and page URL
        # normalization.
        # --------------------------------------------------------------
        (
            document,
            cpu_wait_seconds,
            cpu_work_seconds,
        ) = await self._parse_locations(
            xml=fetched.text,
        )
        
        parse_seconds = (
            cpu_wait_seconds
            + cpu_work_seconds
        )

        _log_resolve_timing(
            mode="locations",
            url=fetched.final_url,
            fetch_seconds=fetch_seconds,
            parse_seconds=parse_seconds,
            cpu_wait_seconds=cpu_wait_seconds,
            cpu_work_seconds=cpu_work_seconds,
            source_type=document.source_type,
            sitemap_count=len(
                document.sitemap_urls
            ),
            page_count=len(
                document.page_urls
            ),
            not_modified=False,
        )

        return ResolvedSitemapLocations(
            document=document,
            fetch=fetched,
            source_url=url,
            final_url=fetched.final_url,
            not_modified=False,
        )


def _log_resolve_timing(
    *,
    mode: str,
    url: str,
    fetch_seconds: float,
    parse_seconds: float | None,
    cpu_wait_seconds: float | None,
    cpu_work_seconds: float | None,
    source_type: str | None,
    sitemap_count: int | None,
    page_count: int | None,
    not_modified: bool,
) -> None:
    """
    Log only relatively slow sitemap resolution stages.

    parse_seconds measures the complete wait for the shared CPU semaphore
    plus asyncio.to_thread(...) execution.

    This is intentional because contention for the sitemap CPU budget is
    part of the behavior being measured.
    """

    slow_fetch = (
        fetch_seconds >= 0.5
    )

    slow_parse = (
        parse_seconds is not None
        and parse_seconds >= 0.25
    )

    if (
        not slow_fetch
        and not slow_parse
    ):
        return

    parse_value = (
        f"{parse_seconds:.3f}s"
        if parse_seconds is not None
        else "-"
    )

    wait_value = (
        f"{cpu_wait_seconds:.3f}s"
        if cpu_wait_seconds is not None
        else "-"
    )

    work_value = (
        f"{cpu_work_seconds:.3f}s"
        if cpu_work_seconds is not None
        else "-"
    )

    AGENTVERSE_LOGGER.info(
        LogDomain.TOOL_BAY,
        (
            "[🕷️ WEB CRAWLER]"
            "[SITE_MAP]"
            "[RESOLVE_TIMING] "
            f"mode={mode} "
            f"url={url} "
            f"fetch={fetch_seconds:.3f}s "
            f"parse={parse_value} "
            f"cpu_wait={wait_value} "
            f"cpu_work={work_value} "
            f"type={source_type or '-'} "
            f"sitemaps="
            f"{sitemap_count if sitemap_count is not None else '-'} "
            f"pages="
            f"{page_count if page_count is not None else '-'} "
            f"not_modified={not_modified}"
        ),
    )