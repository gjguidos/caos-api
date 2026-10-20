from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, TYPE_CHECKING

from pymongo import ReturnDocument, UpdateOne


if TYPE_CHECKING:
    from src.base.repositories.mongodb_repository import (
        MongoDBRepository,
    )


class SitemapUrlInventoryRepository:
    SITEMAP_URL_INVENTORY_COLLECTION = (
        "coll_crawler_sitemap_url_inventory"
    )

    def __init__(
        self,
        repository: MongoDBRepository,
    ) -> None:
        self.repository = repository

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)
    async def ensure_sitemap_url_inventory_indexes(
        self,
    ) -> None:
        collection = await self.repository.get_collection(
            self.SITEMAP_URL_INVENTORY_COLLECTION
        )

        await collection.create_index(
            [
                ("run_id", 1),
                ("url_hash", 1),
            ],
            unique=True,
            name="uq_sitemap_run_url_hash",
        )

        await collection.create_index(
            [
                ("run_id", 1),
                ("status", 1),
                ("discovery_order", 1),
            ],
            name="ix_sitemap_url_iteration",
        )

    async def get_leased_sitemap_urls_by_hashes(
        self,
        *,
        run_id: str,
        lease_id: str,
        url_hashes: list[str],
    ) -> list[dict[str, Any]]:
        if not url_hashes:
            return []

        collection = await self.repository.get_collection(
            self.SITEMAP_URL_INVENTORY_COLLECTION
        )

        cursor = (
            collection
            .find(
                {
                    "run_id": run_id,
                    "lease_id": lease_id,
                    "status": "leased",
                    "url_hash": {
                        "$in": url_hashes,
                    },
                }
            )
            .sort("discovery_order", 1)
        )

        return await cursor.to_list(
            length=len(url_hashes)
        )

    async def add_sitemap_url_batch(
        self,
        *,
        records: list[dict[str, Any]],
    ) -> int:
        if not records:
            return 0

        collection = await self.repository.get_collection(
            self.SITEMAP_URL_INVENTORY_COLLECTION
        )

        operations = [
            UpdateOne(
                {
                    "run_id": record["run_id"],
                    "url_hash": record["url_hash"],
                },
                {
                    "$setOnInsert": record,
                },
                upsert=True,
            )
            for record in records
        ]

        result = await collection.bulk_write(
            operations,
            ordered=False,
        )

        return int(result.upserted_count)
    
    async def complete_sitemap_url_batch(
        self,
        *,
        run_id: str,
        lease_id: str,
        successful_hashes: list[str],
        failed_hashes: list[str],
    ) -> dict[str, int]:
        overlap = set(successful_hashes) & set(failed_hashes)

        if overlap:
            raise ValueError(
                "A URL cannot be both successful and failed."
            )

        collection = await self.repository.get_collection(
            self.SITEMAP_URL_INVENTORY_COLLECTION
        )

        acquired_count = 0
        failed_count = 0
        now = self._now()

        if successful_hashes:
            result = await collection.update_many(
                {
                    "run_id": run_id,
                    "lease_id": lease_id,
                    "status": "leased",
                    "url_hash": {
                        "$in": successful_hashes,
                    },
                },
                {
                    "$set": {
                        "status": "acquired",
                        "acquired_at": now,
                    },
                    "$unset": {
                        "lease_id": "",
                        "lease_until": "",
                        "last_error": "",
                    },
                },
            )

            acquired_count = int(result.modified_count)

        if failed_hashes:
            result = await collection.update_many(
                {
                    "run_id": run_id,
                    "lease_id": lease_id,
                    "status": "leased",
                    "url_hash": {
                        "$in": failed_hashes,
                    },
                },
                {
                    "$set": {
                        "status": "failed",
                        "last_error": "crawl_processing_failed",
                    },
                    "$unset": {
                        "lease_id": "",
                        "lease_until": "",
                        "acquired_at": "",
                    },
                },
            )

            failed_count = int(result.modified_count)

        return {
            "acquired": acquired_count,
            "failed": failed_count,
        }
    
    async def iter_sitemap_urls(
        self,
        *,
        run_id: str,
        status: str | None = None,
        after_discovery_order: int | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        collection = await self.repository.get_collection(
            self.SITEMAP_URL_INVENTORY_COLLECTION
        )

        query: dict[str, Any] = {
            "run_id": run_id,
        }

        if status is not None:
            query["status"] = status

        if after_discovery_order is not None:
            query["discovery_order"] = {
                "$gt": after_discovery_order,
            }

        cursor = (
            collection
            .find(query)
            .sort("discovery_order", 1)
        )

        if limit is not None:
            cursor = cursor.limit(limit)

        async for document in cursor:
            yield document

    async def count_sitemap_urls(
        self,
        *,
        run_id: str,
        status: str | None = None,
    ) -> int:
        collection = await self.repository.get_collection(
            self.SITEMAP_URL_INVENTORY_COLLECTION
        )

        query: dict[str, Any] = {
            "run_id": run_id,
        }

        if status is not None:
            query["status"] = status

        return int(
            await collection.count_documents(query)
        )
    
    async def claim_sitemap_url_batch(
        self,
        *,
        run_id: str,
        lease_id: str,
        batch_size: int,
        lease_until: datetime,
    ) -> list[dict[str, Any]]:
        if not run_id:
            raise ValueError(
                "run_id must not be empty"
            )

        if not lease_id:
            raise ValueError(
                "lease_id must not be empty"
            )

        if batch_size <= 0:
            raise ValueError(
                "batch_size must be greater than zero"
            )

        collection = await self.repository.get_collection(
            self.SITEMAP_URL_INVENTORY_COLLECTION
        )

        claimed: list[dict[str, Any]] = []

        for _ in range(batch_size):
            record = await collection.find_one_and_update(
                {
                    "run_id": run_id,
                    "status": "discovered",
                },
                {
                    "$set": {
                        "status": "leased",
                        "lease_id": lease_id,
                        "lease_until": lease_until,
                        "last_error": None,
                    },
                    "$inc": {
                        "attempt_count": 1,
                    },
                },
                sort=[
                    ("discovery_order", 1),
                ],
                return_document=ReturnDocument.AFTER,
            )

            if record is None:
                break

            claimed.append(record)

        return claimed
    
    async def delete_sitemap_url_inventory(
        self,
        *,
        run_id: str,
    ) -> int:
        collection = await self.repository.get_collection(
            self.SITEMAP_URL_INVENTORY_COLLECTION
        )

        result = await collection.delete_many(
            {
                "run_id": run_id,
            }
        )

        return int(result.deleted_count)
