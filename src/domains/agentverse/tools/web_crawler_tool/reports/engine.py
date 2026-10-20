from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import date, datetime
from typing import Any

from src.domains.agentverse.tools.web_crawler_tool.asset_state.store import (
    AssetStateStore,
)
from src.domains.agentverse.tools.web_crawler_tool.items.documents import (
    document_item_from_asset_observation,
)
from src.domains.agentverse.tools.web_crawler_tool.items.media import (
    media_item_from_asset_observation,
)
from src.domains.agentverse.tools.web_crawler_tool.reader.models import (
    ReportDownload,
)
from src.domains.agentverse.tools.web_crawler_tool.reports.crawl_run_report_store import (
    CrawlRunReportRecord,
    CrawlRunReportStore,
)


class ReportEngine:
    """Render persisted crawl reports into downloadable representations."""

    def __init__(
        self,
        *,
        report_store: CrawlRunReportStore,
        asset_store: AssetStateStore,
    ) -> None:
        self.report_store = report_store
        self.asset_store = asset_store

    async def create_download(
        self,
        *,
        run_id: str,
        report_id: str,
    ) -> ReportDownload | None:
        normalized_report_id = self._normalize_report_id(report_id)

        record = await self.report_store.get(run_id=run_id)
        if record is None:
            return None

        if normalized_report_id == "summary":
            return self._create_json_download(
                filename=f"{run_id}-summary.json",
                payload=self._build_summary(record),
            )

        if normalized_report_id == "metadata":
            return self._create_json_download(
                filename=f"{run_id}-metadata.json",
                payload=self._build_metadata(record),
            )
        
        if normalized_report_id == "asset_light":
            return ReportDownload(
                filename=f"{run_id}-asset-light.json",
                media_type="application/json",
                body=self._stream_asset_light_report(
                    record=record,
                ),
            )

        if normalized_report_id == "media":
            return ReportDownload(
                filename=f"{run_id}-media.json",
                media_type="application/json",
                body=self._stream_asset_report(
                    record=record,
                    report_id="media",
                    asset_type="media",
                ),
            )

        if normalized_report_id == "documents":
            return ReportDownload(
                filename=f"{run_id}-documents.json",
                media_type="application/json",
                body=self._stream_asset_report(
                    record=record,
                    report_id="documents",
                    asset_type="document",
                ),
            )

        report = record.reports.get(normalized_report_id)
        if report is None:
            return None

        return self._create_json_download(
            filename=f"{run_id}-{normalized_report_id}.json",
            payload={
                "run_id": run_id,
                "report_id": normalized_report_id,
                "status": record.status,
                "report": report,
            },
        )

    def _create_json_download(
        self,
        *,
        filename: str,
        payload: Any,
    ) -> ReportDownload:
        return ReportDownload(
            filename=filename,
            media_type="application/json",
            body=self._single_json_body(payload),
        )

    async def _single_json_body(
        self,
        payload: Any,
    ) -> AsyncIterator[bytes]:
        yield self._encode_json(payload)

    async def _stream_asset_report(
        self,
        *,
        record: CrawlRunReportRecord,
        report_id: str,
        asset_type: str,
    ) -> AsyncIterator[bytes]:
        prefix = {
            "run_id": record.run_id,
            "report_id": report_id,
            "status": record.status,
            "summary": record.reports.get(report_id) or {},
        }

        encoded_prefix = self._encode_json(prefix)
        yield encoded_prefix[:-1]
        yield b',"items":['

        first = True

        async for observation in self.asset_store.iter_run_observations(
            run_id=record.run_id,
            asset_type=asset_type,
        ):
            item = self._asset_item(
                report_id=report_id,
                observation=observation,
            )
            if item is None:
                continue

            if not first:
                yield b","

            first = False
            yield self._encode_json(item)

        yield b"]}"

    async def _stream_asset_light_report(
        self,
        *,
        record: CrawlRunReportRecord,
    ) -> AsyncIterator[bytes]:
        yield b"["

        first = True

        async for observation in (
            self.asset_store.iter_run_observations(
                run_id=record.run_id,
                asset_type="media",
            )
        ):
            subtype = str(
                observation.get("subtype")
                or ""
            ).strip().lower()

            if subtype != "image":
                continue

            image_url = str(
                observation.get("url")
                or ""
            ).strip()

            source_page = str(
                observation.get("source_page")
                or ""
            ).strip()

            if not image_url or not source_page:
                continue

            item = {
                "subsite": source_page,
                "ImageUrl": image_url,
                "startDate": (
                    record.created_at
                    .date()
                    .isoformat()
                ),
            }

            if not first:
                yield b","

            first = False

            yield self._encode_json(
                item
            )

        yield b"]"

    @staticmethod
    def _asset_item(
        *,
        report_id: str,
        observation: dict[str, Any],
    ) -> dict[str, Any] | None:
        if report_id == "media":
            item = media_item_from_asset_observation(
                observation=observation,
            )
        elif report_id == "documents":
            item = document_item_from_asset_observation(
                observation=observation,
            )
        else:
            return None

        if item is None:
            return None

        return item.model_dump(mode="json")

    @staticmethod
    def _build_summary(
        record: CrawlRunReportRecord,
    ) -> dict[str, Any]:
        reports = record.reports or {}

        return {
            "run_id": record.run_id,
            "status": record.status,
            "pages_parsed": record.pages_parsed,
            "pages_failed": record.pages_failed,
            "page_totals": reports.get("page_totals") or {},
            "available_reports": sorted(reports.keys()),
            "created_at": record.created_at,
            "updated_at": record.updated_at,
            "completed_at": record.completed_at,
            "last_error": record.last_error,
        }

    @staticmethod
    def _build_metadata(
        record: CrawlRunReportRecord,
    ) -> dict[str, Any]:
        return {
            "run_id": record.run_id,
            **(record.metadata or {}),
        }

    @staticmethod
    def _normalize_report_id(
        report_id: str,
    ) -> str:
        normalized = report_id.strip().lower()

        aliases = {
            "crawl_summary": "summary",
            "images": "media",
            "image": "media",
            "docs": "documents",
            "doc": "documents",
            "document": "documents",
        }

        return aliases.get(normalized, normalized)

    @classmethod
    def _encode_json(
        cls,
        value: Any,
    ) -> bytes:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            default=cls._json_default,
        ).encode("utf-8")

    @staticmethod
    def _json_default(
        value: Any,
    ) -> Any:
        if isinstance(value, datetime | date):
            return value.isoformat()

        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")

        return str(value)
