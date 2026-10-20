from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, Field

SITEMAP_URL_INVENTORY_COLLECTION = (
    "coll_crawler_sitemap_url_inventory"
)


class SitemapUrlRecord(BaseModel):
    run_id: str
    url: str
    url_hash: str
    source_sitemap_url: str
    discovery_order: int = Field(ge=0)

    status: Literal[
        "discovered",
        "leased",
        "acquired",
        "failed",
    ] = "discovered"

    discovered_at: datetime
    attempt_count: int = Field(default=0, ge=0)
    lease_id: str | None = None
    lease_until: datetime | None = None
    last_error: str | None = None
    acquired_at: datetime | None = None


class SitemapUrlInventoryDatabase(Protocol):
    async def ensure_sitemap_url_inventory_indexes(
        self,
    ) -> None:
        ...

    async def add_sitemap_url_batch(
        self,
        *,
        records: list[dict[str, Any]],
    ) -> int:
        ...

    async def complete_sitemap_url_batch(
        self,
        *,
        run_id: str,
        lease_id: str,
        successful_hashes: list[str],
        failed_hashes: list[str],
    ) -> dict[str, int]:
        ...

    async def claim_sitemap_url_batch(
        self,
        *,
        run_id: str,
        lease_id: str,
        batch_size: int,
        lease_until: datetime,
    ) -> list[dict[str, Any]]:
        ...

    async def count_sitemap_urls(
        self,
        *,
        run_id: str,
        status: str | None = None,
    ) -> int:
        ...

    async def delete_sitemap_url_inventory(
        self,
        *,
        run_id: str,
    ) -> int:
        ...
    
    def iter_sitemap_urls(
        self,
        *,
        run_id: str,
        status: str | None = None,
        after_discovery_order: int | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        ...

    async def get_leased_sitemap_urls_by_hashes(
        self,
        *,
        run_id: str,
        lease_id: str,
        url_hashes: list[str],
    ) -> list[dict[str, Any]]:
        ...

    async def renew_sitemap_url_batch(
        self,
        *,
        run_id: str,
        lease_id: str,
        lease_until: datetime,
    ) -> int:
        ...


class SitemapUrlInventoryStore:
    def __init__(
        self,
        *,
        db_service: SitemapUrlInventoryDatabase,
    ) -> None:
        self.db_service = db_service

    async def ensure_indexes(self) -> None:
        await (
            self.db_service
            .ensure_sitemap_url_inventory_indexes()
        )

    async def add_urls(
        self,
        *,
        run_id: str,
        source_sitemap_url: str,
        urls: list[str],
        starting_order: int,
    ) -> int:
        now = datetime.now(UTC)
        records: list[dict[str, Any]] = []
        seen: set[str] = set()

        for offset, raw_url in enumerate(urls):
            url = normalize_url(raw_url)
            if not url:
                continue

            url_hash = make_url_hash(url)
            if url_hash in seen:
                continue

            seen.add(url_hash)
            records.append(
                SitemapUrlRecord(
                    run_id=run_id,
                    url=url,
                    url_hash=url_hash,
                    source_sitemap_url=(
                        normalize_url(source_sitemap_url)
                    ),
                    discovery_order=starting_order + offset,
                    discovered_at=now,
                ).model_dump(mode="python")
            )

        if not records:
            return 0

        return await self.db_service.add_sitemap_url_batch(
            records=records,
        )
    
    async def get_leased_urls_by_hashes(
        self,
        *,
        run_id: str,
        lease_id: str,
        url_hashes: list[str],
    ) -> list[SitemapUrlRecord]:
        if not url_hashes:
            return []

        raw_records = (
            await self.db_service
            .get_leased_sitemap_urls_by_hashes(
                run_id=run_id,
                lease_id=lease_id,
                url_hashes=url_hashes,
            )
        )

        return [
            SitemapUrlRecord.model_validate(record)
            for record in raw_records
        ]
    
    async def iter_batches(
        self,
        *,
        run_id: str,
        batch_size: int,
        status: str | None = "discovered",
        max_urls: int | None = None,
    ) -> AsyncIterator[list[SitemapUrlRecord]]:
        if batch_size <= 0:
            raise ValueError(
                "batch_size must be greater than zero"
            )

        remaining = max_urls
        after_discovery_order: int | None = None

        while remaining is None or remaining > 0:
            limit = batch_size

            if remaining is not None:
                limit = min(limit, remaining)

            batch: list[SitemapUrlRecord] = []

            async for record in self.iter_urls(
                run_id=run_id,
                status=status,
                after_discovery_order=after_discovery_order,
                limit=limit,
            ):
                batch.append(record)

            if not batch:
                return

            yield batch

            after_discovery_order = (
                batch[-1].discovery_order
            )

            if remaining is not None:
                remaining -= len(batch)


    async def iter_urls(
        self,
        *,
        run_id: str,
        status: str | None = "discovered",
        after_discovery_order: int | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[SitemapUrlRecord]:
        async for raw in self.db_service.iter_sitemap_urls(
            run_id=run_id,
            status=status,
            after_discovery_order=after_discovery_order,
            limit=limit,
        ):
            yield SitemapUrlRecord.model_validate(raw)

    async def count(
        self,
        *,
        run_id: str,
        status: str | None = None,
    ) -> int:
        return await self.db_service.count_sitemap_urls(
            run_id=run_id,
            status=status,
        )

    async def delete_run(
        self,
        *,
        run_id: str,
    ) -> int:
        return await (
            self.db_service
            .delete_sitemap_url_inventory(
                run_id=run_id,
            )
        )
    
    async def renew_sitemap_url_batch(
        self,
        *,
        run_id: str,
        lease_id: str,
        lease_until: datetime,
    ) -> int:
        return await (
            self.db_service.renew_sitemap_url_batch(
                run_id=run_id,
                lease_id=lease_id,
                lease_until=lease_until,
            )
        )
    
    async def claim_batch(
        self,
        *,
        run_id: str,
        lease_id: str,
        batch_size: int,
        lease_until: datetime,
    ) -> list[SitemapUrlRecord]:
        if batch_size <= 0:
            raise ValueError(
                "batch_size must be greater than zero"
            )

        if not lease_id:
            raise ValueError(
                "lease_id must not be empty"
            )

        raw_records = (
            await self.db_service.claim_sitemap_url_batch(
                run_id=run_id,
                lease_id=lease_id,
                batch_size=batch_size,
                lease_until=lease_until,
            )
        )

        return [
            SitemapUrlRecord.model_validate(record)
            for record in raw_records
        ]
    
    async def complete_batch(
        self,
        *,
        run_id: str,
        lease_id: str,
        successful_hashes: list[str],
        failed_hashes: list[str],
    ) -> dict[str, int]:
        return await (
            self.db_service
            .complete_sitemap_url_batch(
                run_id=run_id,
                lease_id=lease_id,
                successful_hashes=successful_hashes,
                failed_hashes=failed_hashes,
            )
        )


def normalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        return ""

    return urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path or "/",
            parts.query,
            "",
        )
    )


def make_url_hash(url: str) -> str:
    return sha256(
        normalize_url(url).encode("utf-8")
    ).hexdigest()
