from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from src.base.authentication.rbac import require_roles_and_scopes
from src.base.authentication.role import Role
from src.base.entities.user import User
from src.domains.agentverse.tools.web_crawler_tool.reader.interface import (
    CrawlReportReaderInterface,
)

router = APIRouter()

async def get_crawl_report_reader(
    request: Request,
) -> CrawlReportReaderInterface:
    return (
        request.app.container.agentverse
        .web_crawler()
        .crawl_report_reader()
    )


async def _download_report(
    *,
    run_id: str,
    report_id: str,
    reader: CrawlReportReaderInterface,
) -> StreamingResponse:
    download = await reader.create_download(
        run_id=run_id,
        report_id=report_id,
    )
    if download is None:
        raise HTTPException(
            status_code=404,
            detail=f"Report {report_id!r} was not found for this run.",
        )

    filename = quote(download.filename, safe="")
    return StreamingResponse(
        download.body,
        media_type=download.media_type,
        headers={
            "Content-Disposition": (
                f"attachment; filename*=UTF-8''{filename}"
            ),
        },
    )


@router.get(
    "/api/v1/operations/{run_id}/reports/summary",
    tags=["Web Crawler Reports"],
)
async def download_crawl_summary(
    run_id: str,
    reader: CrawlReportReaderInterface = Depends(get_crawl_report_reader),
    user: User = Depends(
        require_roles_and_scopes(required_roles=[[Role.ADMIN]])
    ),
) -> StreamingResponse:
    return await _download_report(
        run_id=run_id,
        report_id="summary",
        reader=reader,
    )

@router.get(
    "/api/v1/operations/{run_id}/reports/metadata",
    tags=["Web Crawler Reports"],
)
async def download_crawl_metadata(
    run_id: str,
    reader: CrawlReportReaderInterface = Depends(get_crawl_report_reader),
    user: User = Depends(
        require_roles_and_scopes(required_roles=[[Role.ADMIN]])
    ),
) -> StreamingResponse:
    return await _download_report(
        run_id=run_id,
        report_id="metadata",
        reader=reader,
    )


@router.get(
    "/api/v1/operations/test",
    tags=["Web Crawler Reports"],
)
async def test(
    run_id: str,
    reader: CrawlReportReaderInterface = Depends(get_crawl_report_reader),
    user: User = Depends(
        require_roles_and_scopes(required_roles=[[Role.ADMIN]])
    ),
) -> str:
    return "Test successful"



@router.get(
    "/api/v1/operations/{run_id}/reports/media/stream",
    tags=["Web Crawler Reports"],
)
async def download_media_report(
    run_id: str,
    reader: CrawlReportReaderInterface = Depends(get_crawl_report_reader),
    user: User = Depends(
        require_roles_and_scopes(required_roles=[[Role.ADMIN]])
    ),
) -> StreamingResponse:
    return await _download_report(
        run_id=run_id,
        report_id="media",
        reader=reader,
    )


@router.get(
    "/api/v1/operations/{run_id}/reports/documents/stream",
    tags=["Web Crawler Reports"],
)
async def download_documents_report(
    run_id: str,
    reader: CrawlReportReaderInterface = Depends(get_crawl_report_reader),
    user: User = Depends(
        require_roles_and_scopes(required_roles=[[Role.ADMIN]])
    ),
) -> StreamingResponse:
    return await _download_report(
        run_id=run_id,
        report_id="documents",
        reader=reader,
    )


@router.get(
    "/api/v1/operations/{run_id}/reports/{report_id}",
    tags=["Web Crawler Reports"],
)
async def download_report(
    run_id: str,
    report_id: str,
    reader: CrawlReportReaderInterface = Depends(get_crawl_report_reader),
    user: User = Depends(
        require_roles_and_scopes(required_roles=[[Role.ADMIN]])
    ),
) -> StreamingResponse:
    return await _download_report(
        run_id=run_id,
        report_id=report_id,
        reader=reader,
    )
