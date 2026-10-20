from __future__ import annotations

from typing import Protocol

from src.base.queues.models import AgentVerseJob
from src.domains.agentverse.tools.web_crawler_tool.contributions.models import (
    REPORT_CONTRIBUTION_SCHEMA,
    ReportContributionPayload,
)
from src.domains.agentverse.tools.web_crawler_tool.reports.crawl_run_report_store import (
    CrawlRunReportStore,
)
from src.domains.agentverse.tools.web_crawler_tool.sitemap.url_inventory_store import (
    SitemapUrlInventoryStore,
)


class MediaContributionSink(Protocol):
    async def persist_media_items(
        self,
        *,
        run_id: str,
        source_page_url: str,
        media_items: list[dict],
    ) -> None:
        ...


class ReportContributionConsumer:
    def __init__(
        self,
        *,
        report_store: CrawlRunReportStore,
        inventory_store: SitemapUrlInventoryStore,
        media_sink: MediaContributionSink | None = None,
    ) -> None:
        self.report_store = report_store
        self.inventory_store = inventory_store
        self.media_sink = media_sink

    async def process_job_without_ack(
        self,
        *,
        job: AgentVerseJob,
    ) -> None:
        if job.schema_name != REPORT_CONTRIBUTION_SCHEMA:
            raise ValueError(
                "Unsupported report contribution schema: "
                f"{job.schema_name}"
            )

        payload = ReportContributionPayload.model_validate(
            job.payload
        )

        if payload.media_items:
            if self.media_sink is None:
                raise RuntimeError(
                    "Media items were included, but no media sink "
                    "was configured."
                )

            await self.media_sink.persist_media_items(
                run_id=payload.run_id,
                source_page_url=payload.source_page_url,
                media_items=payload.media_items,
            )

        # This operation is idempotent through the page contribution
        # marker in CrawlRunReportStore/CrawlerDBService.
        await self.report_store.apply_page_report_contribution(
            run_id=payload.run_id,
            page_id=payload.page_id,
            increments=payload.increments,
            add_to_sets=payload.add_to_sets,
        )

        if payload.url_hash:
            if not payload.lease_id:
                raise RuntimeError(
                    "Missing lease_id for report contribution: "
                    f"{payload.page_id}"
                )

            await self.inventory_store.complete_batch(
                run_id=payload.run_id,
                lease_id=payload.lease_id,
                successful_hashes=[payload.url_hash],
                failed_hashes=[],
            )