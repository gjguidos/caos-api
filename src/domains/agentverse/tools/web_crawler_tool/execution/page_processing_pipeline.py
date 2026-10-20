from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from typing import Protocol

from src.domains.agentverse.logging.logger_v1 import (
    AGENTVERSE_LOGGER,
    LogDomain,
)
from src.domains.agentverse.tools.web_crawler_tool.analysis.analysis_parser import (
    PageAnalysisParser,
)
from src.domains.agentverse.tools.web_crawler_tool.analysis.models import (
    PageAnalysis,
)
from src.domains.agentverse.tools.web_crawler_tool.execution.crawler_cpu_executor import (
    page_cpu_executor,
)
from src.domains.agentverse.tools.web_crawler_tool.execution.models import (
    ProcessedPagePayload,
    ProcessedPageRecord,
)
from src.domains.agentverse.tools.web_crawler_tool.fetchers.models import (
    FetchedPage,
)
from src.domains.agentverse.tools.web_crawler_tool.site_state.models import (
    CrawlTarget,
)


class PageAnalysisSink(Protocol):
    async def __call__(
        self,
        *,
        payload: ProcessedPagePayload,
        record: ProcessedPageRecord,
        analysis: PageAnalysis,
    ) -> None:
        ...


class CrawlUrlInventory(Protocol):
    async def mark_acquired(
        self,
        *,
        run_id: str,
        lease_id: str,
        url_hash: str,
    ) -> None:
        ...

    async def mark_failed(
        self,
        *,
        run_id: str,
        lease_id: str,
        url_hash: str,
        error: str,
    ) -> None:
        ...
        
    async def complete_batch(
        self,
        *,
        run_id: str,
        lease_id: str,
        successful_hashes: list[str],
        failed_hashes: list[str],
    ) -> dict[str, int]:
        ...

    


class PageProcessingPipeline:
    def __init__(
        self,
        *,
        run_id: str,
        inventory_store: CrawlUrlInventory,
        parser: PageAnalysisParser,
        on_page_analyzed: PageAnalysisSink,
    ) -> None:
        if not run_id:
            raise ValueError("run_id must not be empty")

        self.run_id = run_id
        self.inventory_store = inventory_store
        self.parser = parser
        self.on_page_analyzed = on_page_analyzed

    async def handle(
        self,
        target: CrawlTarget,
        page: FetchedPage,
    ) -> None:
        inventory_url_hash = _url_hash_from_target(target)
        lease_id = target.lease_id

        # A sitemap source label alone is not enough to indicate that this
        # target belongs to the leased sitemap inventory. Site-coverage plans
        # may contain legacy targets labeled "sitemap" without inventory
        # identity. Only manage inventory records when the target carries the
        # lease and hash needed to complete them.
        inventory_managed = (
            target.source == "sitemap"
            and bool(inventory_url_hash)
            and bool(lease_id)
        )

        if target.source == "sitemap" and lease_id and not inventory_url_hash:
            raise RuntimeError(
                f"Missing url_hash for sitemap URL: {target.url}"
            )

        if target.source == "sitemap" and inventory_url_hash and not lease_id:
            raise RuntimeError(
                f"Missing lease_id for sitemap URL: {target.url}"
            )

        page_identity = inventory_url_hash or _url_hash_from_url(target.url)

        page_id = _page_id(
            run_id=self.run_id,
            url_hash=page_identity,
        )

        try:
            body = _page_body(page)

            html = body.decode(
                "utf-8",
                errors="replace",
            )
            
            analysis = await page_cpu_executor.run(
                    self.parser.parse,
                    url=page.url,
                    final_url=page.final_url,
                    status_code=page.status_code,
                    content_type=page.content_type,
                    html=html,
                )

            record = ProcessedPageRecord(
                page_id=page_id,
                run_id=self.run_id,
                url=page.url,
                final_url=page.final_url,
                status_code=page.status_code,
                content_type=page.content_type,
                processed_at=datetime.now(UTC),
            )

            payload = ProcessedPagePayload(
                page_id=page_id,
                run_id=self.run_id,
                lease_id=lease_id,
                # Site-coverage targets are not leased in the sitemap
                # inventory. Keep their identity in page_id, but omit the
                # inventory marker so report consumers do not complete a
                # sitemap lease for them.
                url_hash=(
                    inventory_url_hash
                    if inventory_managed
                    else None
                ),
            )

            await self.on_page_analyzed(
                payload=payload,
                record=record,
                analysis=analysis,
            )


            if inventory_managed:
                await self.inventory_store.complete_batch(
                    run_id=self.run_id,
                    lease_id=lease_id,
                    successful_hashes=[inventory_url_hash],
                    failed_hashes=[],
                )

        except Exception:

            if inventory_managed:
                try:
                    await self.inventory_store.complete_batch(
                        run_id=self.run_id,
                        lease_id=lease_id,
                        successful_hashes=[],
                        failed_hashes=[inventory_url_hash],
                    )
                except Exception:
                    AGENTVERSE_LOGGER.exception(
                        LogDomain.TOOL_BAY,
                        (
                            "[WEB CRAWLER][PAGE][FAILED] "
                            f"run_id={self.run_id} "
                            f"url_hash={inventory_url_hash} "
                            "failed to mark url as failed in inventory"
                        ),
                    )

            raise


def _url_hash_from_target(
    target: CrawlTarget,
) -> str | None:
    value = target.metadata.get("url_hash")

    if isinstance(value, str) and value:
        return value

    return None


def _page_id(
    *,
    run_id: str,
    url_hash: str,
) -> str:
    return sha256(
        f"{run_id}:{url_hash}".encode()
    ).hexdigest()


def _url_hash_from_url(url: str) -> str:
    return sha256(url.encode("utf-8")).hexdigest()


def _page_body(page: FetchedPage) -> bytes:
    body = getattr(page, "body", None)

    if isinstance(body, bytes):
        return body

    if isinstance(body, bytearray):
        return bytes(body)

    html = getattr(page, "html", None)

    if isinstance(html, str):
        return html.encode("utf-8")

    raise TypeError(
        "FetchedPage must provide HTML as bytes in 'body' "
        "or as text in 'html'"
    )
