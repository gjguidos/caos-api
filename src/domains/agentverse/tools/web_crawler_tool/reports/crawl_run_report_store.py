from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

CRAWL_RUN_REPORT_COLLECTION = "coll_crawler_run_reports"

CrawlRunReportStatus = Literal[
    "running",
    "complete",
    "failed",
]


class CrawlRunReportRecord(BaseModel):
    model_config = ConfigDict(extra="ignore")

    run_id: str
    status: CrawlRunReportStatus = "running"

    pages_parsed: int = Field(default=0, ge=0)
    pages_failed: int = Field(default=0, ge=0)

    reports: dict[str, Any] = Field(
        default_factory=dict,
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
    )

    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    last_error: str | None = None


class CrawlRunReportDatabase(Protocol):
    async def ensure_crawl_run_report_indexes(
        self,
    ) -> None:
        ...

    async def apply_page_report_contribution(
        self,
        *,
        run_id: str,
        page_id: str,
        increments: dict[str, int],
        set_values: dict[str, Any] | None = None,
        add_to_sets: dict[str, list[Any]] | None = None,
    ) -> bool:
        ...

    async def get_crawl_run_report(
        self,
        *,
        run_id: str,
    ) -> dict[str, Any] | None:
        ...

    async def set_crawl_run_report_metadata(
        self,
        *,
        run_id: str,
        metadata: dict[str, Any],
    ) -> None:
        ...

    async def mark_crawl_run_report_complete(
        self,
        *,
        run_id: str,
        pages_parsed: int,
        pages_failed: int,
    ) -> None:
        ...

    async def mark_crawl_run_report_failed(
        self,
        *,
        run_id: str,
        error: str,
    ) -> None:
        ...

    async def delete_crawl_run_report(
        self,
        *,
        run_id: str,
    ) -> None:
        ...


class CrawlRunReportStore:
    def __init__(
        self,
        *,
        db_service: CrawlRunReportDatabase,
    ) -> None:
        self.db_service = db_service

    async def ensure_indexes(self) -> None:
        await (
            self.db_service
            .ensure_crawl_run_report_indexes()
        )

    async def apply_page_report_contribution(
        self,
        *,
        run_id: str,
        page_id: str,
        increments: Mapping[str, int],
        add_to_sets: Mapping[str, list[Any]] | None = None,
    ) -> bool:
        if not run_id:
            raise ValueError(
                "run_id must not be empty"
            )

        if not page_id:
            raise ValueError(
                "page_id must not be empty"
            )

        normalized_increments = {
            str(path): int(value)
            for path, value in increments.items()
            if int(value) != 0
        }

        normalized_add_to_sets = (
            {
                str(path): [
                    value
                    for value in values
                    if value is not None
                ]
                for path, values in add_to_sets.items()
                if values
            }
            if add_to_sets
            else None
        )

        return await (
            self.db_service
            .apply_page_report_contribution(
                run_id=run_id,
                page_id=page_id,
                increments=normalized_increments,
                add_to_sets=normalized_add_to_sets,
            )
        )

    async def get(
        self,
        *,
        run_id: str,
    ) -> CrawlRunReportRecord | None:
        record = await (
            self.db_service
            .get_crawl_run_report(
                run_id=run_id,
            )
        )

        if record is None:
            return None

        return CrawlRunReportRecord.model_validate(
            record
        )

    async def set_metadata(
        self,
        *,
        run_id: str,
        metadata: Mapping[str, Any],
    ) -> None:
        await (
            self.db_service
            .set_crawl_run_report_metadata(
                run_id=run_id,
                metadata=dict(metadata),
            )
        )

    async def mark_complete(
        self,
        *,
        run_id: str,
        pages_parsed: int,
        pages_failed: int,
    ) -> None:
        await (
            self.db_service
            .mark_crawl_run_report_complete(
                run_id=run_id,
                pages_parsed=pages_parsed,
                pages_failed=pages_failed,
            )
        )

    async def mark_failed(
        self,
        *,
        run_id: str,
        error: str,
    ) -> None:
        await (
            self.db_service
            .mark_crawl_run_report_failed(
                run_id=run_id,
                error=error,
            )
        )

    async def delete(
        self,
        *,
        run_id: str,
    ) -> None:
        await (
            self.db_service
            .delete_crawl_run_report(
                run_id=run_id,
            )
        )