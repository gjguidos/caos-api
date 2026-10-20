from __future__ import annotations

import asyncio

from src.base.operations.models import OperationStatus
from src.domains.agentverse.tools.web_crawler_tool.contributions.constants import (
    report_queue_name,
)
from src.domains.agentverse.tools.web_crawler_tool.streams.constants import (
    acquisition_queue_name,
)


class WebCrawlerCleanupService:
    def __init__(
        self,
        *,
        sitemap_inventory,
        report_repository,
        queue_service,

    ) -> None:
        self.sitemap_inventory = sitemap_inventory
        self.report_repository = report_repository
        self.queue_service = queue_service

    async def cleanup_run(
        self,
        *,
        run_id: str,
    ) -> None:
        await asyncio.gather(
            self.queue_service.delete_queue(
                queue_name=acquisition_queue_name(run_id),
            ),
            self.queue_service.delete_queue(
                queue_name=report_queue_name(run_id),
            ),
        )

    async def finalize_run(
        self,
        *,
        run_id: str,
        status: OperationStatus,
    ) -> None:
        await self.cleanup_run(run_id=run_id)

        await self.sitemap_inventory.delete_run(
            run_id=run_id,
        )


    async def abort_run(
        self,
        *,
        run_id: str,
    ) -> None:
        await self.cleanup_run(run_id=run_id)

        await self.sitemap_inventory.release_run_leases(
            run_id=run_id,
        )
