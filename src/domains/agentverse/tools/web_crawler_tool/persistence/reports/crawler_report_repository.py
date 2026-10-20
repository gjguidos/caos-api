from __future__ import annotations

import asyncio
import random
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pymongo.errors import DuplicateKeyError, OperationFailure

if TYPE_CHECKING:
    from src.base.repositories.mongodb_repository import (
        MongoDBRepository,
    )


class CrawlReportRepository:
    CRAWL_RUN_REPORTS_COLLECTION = (
        "coll_crawler_run_reports"
    )
    CRAWL_REPORT_CONTRIBUTIONS_COLLECTION = (
        "coll_crawler_report_contributions"
    )

    def __init__(
        self,
        repository: MongoDBRepository,
    ) -> None:
        self.repository = repository

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)
    
    async def ensure_crawl_run_report_indexes(self) -> None:
        reports = await self.repository.get_collection(
            self.CRAWL_RUN_REPORTS_COLLECTION,
        )
        contributions = await self.repository.get_collection(
            self.CRAWL_REPORT_CONTRIBUTIONS_COLLECTION,
        )

        await reports.create_index(
            [("run_id", 1)],
            unique=True,
            name="uq_crawler_run_report",
        )

        await contributions.create_index(
            [("run_id", 1), ("page_id", 1)],
            unique=True,
            name="uq_crawler_report_contribution",
        )
    
    async def get_crawl_run_report(
        self,
        *,
        run_id: str,
    ) -> dict[str, Any] | None:
        collection = await self.repository.get_collection(
            self.CRAWL_RUN_REPORTS_COLLECTION,
        )

        document = await collection.find_one(
            {"run_id": run_id},
            {"_id": 0},
        )

        if document is None:
            return None

        if "created_at" not in document:
            created_at = (
                document.get("updated_at")
                or self._now()
            )

            await collection.update_one(
                {
                    "run_id": run_id,
                    "created_at": {
                        "$exists": False,
                    },
                },
                {
                    "$set": {
                        "created_at": created_at,
                    },
                },
            )

            document["created_at"] = created_at

        return document

    async def apply_page_report_contribution(
        self,
        *,
        run_id: str,
        page_id: str,
        increments: dict[str, int],
        set_values: dict[str, Any] | None = None,
        add_to_sets: dict[str, list[Any]] | None = None,
    ) -> bool:
        reports = await self.repository.get_collection(
            self.CRAWL_RUN_REPORTS_COLLECTION,
        )
        contributions = await self.repository.get_collection(
            self.CRAWL_REPORT_CONTRIBUTIONS_COLLECTION,
        )

        mongo_client = reports.database.client

        for attempt in range(6):
            try:
                async with await mongo_client.start_session() as session:
                    return await session.with_transaction(
                        lambda transaction_session:
                            self._apply_page_report_contribution_transaction(
                                reports=reports,
                                contributions=contributions,
                                session=transaction_session,
                                run_id=run_id,
                                page_id=page_id,
                                increments=increments,
                                set_values=set_values,
                                add_to_sets=add_to_sets,
                            )
                    )

            except DuplicateKeyError:
                # Another worker already applied this page.
                return False

            except OperationFailure as exc:
                if not self._is_retryable(
                    exc
                ) or attempt == 5:
                    raise

                await asyncio.sleep(
                    min(0.25, 0.02 * (2**attempt))
                    + random.uniform(0, 0.02)
                )

        raise RuntimeError(
            "Unable to apply page report contribution"
        )

    async def set_crawl_run_report_metadata(
        self,
        *,
        run_id: str,
        metadata: dict[str, Any],
    ) -> None:
        collection = await self.repository.get_collection(
            self.CRAWL_RUN_REPORTS_COLLECTION,
        )

        now = self._now()

        await collection.update_one(
            {"run_id": run_id},
            {
                "$setOnInsert": {
                    "run_id": run_id,
                    "created_at": now,
                    "reports": {},
                    "status": "running",
                },
                "$set": {
                    "metadata": metadata,
                    "updated_at": now,
                },
            },
            upsert=True,
        )

    async def mark_crawl_run_report_complete(
        self,
        *,
        run_id: str,
        pages_parsed: int,
        pages_failed: int,
    ) -> None:
        collection = await self.repository.get_collection(
            self.CRAWL_RUN_REPORTS_COLLECTION,
        )

        now = self._now()

        await collection.update_one(
            {"run_id": run_id},
            {
                "$setOnInsert": {
                    "run_id": run_id,
                    "created_at": now,
                    "reports": {},
                    "last_error": None,
                },
                "$set": {
                    "status": "complete",
                    "pages_parsed": pages_parsed,
                    "pages_failed": pages_failed,
                    "completed_at": now,
                    "updated_at": now,
                },
            },
            upsert=True,
        )

    async def _apply_page_report_contribution_transaction(
        self,
        *,
        reports: Any,
        contributions: Any,
        session: Any,
        run_id: str,
        page_id: str,
        increments: dict[str, int],
        set_values: dict[str, Any] | None,
        add_to_sets: dict[str, list[Any]] | None,
    ) -> bool:
        now = self._now()

        marker = await contributions.update_one(
            {
                "run_id": run_id,
                "page_id": page_id,
            },
            {
                "$setOnInsert": {
                    "run_id": run_id,
                    "page_id": page_id,
                    "applied_at": now,
                },
            },
            upsert=True,
            session=session,
        )

        if marker.upserted_id is None:
            return False

        update: dict[str, Any] = {
            "$setOnInsert": {
                "run_id": run_id,
                "created_at": now,
            },
            "$set": {
                "updated_at": now,
                "status": "running",
            },
        }

        if increments:
            update["$inc"] = increments

        if set_values:
            update["$set"].update(set_values)

        if add_to_sets:
            add_to_set = {
                path: {
                    "$each": [
                        value
                        for value in values
                        if value is not None
                    ],
                }
                for path, values in add_to_sets.items()
                if any(value is not None for value in values)
            }

            if add_to_set:
                update["$addToSet"] = add_to_set

        await reports.update_one(
            {"run_id": run_id},
            update,
            upsert=True,
            session=session,
        )

        return True

    @staticmethod
    def _is_retryable(
        exc: OperationFailure,
    ) -> bool:
        return (
            exc.code == 112
            or exc.has_error_label(
                "TransientTransactionError"
            )
            or exc.has_error_label(
                "UnknownTransactionCommitResult"
            )
        )